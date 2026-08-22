#!/usr/bin/env python3
"""foreman_dispatcher.py — AUTO-3 foreman auto-dispatcher (wq#715). Spec: DESIGN.md.

A stateless-per-pass, one-shot scanner: check kill switches, take the single-instance
slock, scan open tickets in the config's lane allowlist, run every candidate through a
fail-closed decision pipeline (Frank-gate, fire-approval, deps, budget, lane caps,
concurrency), hand survivors to the existing dispatch stack (router.dispatch_cmd via
spawner.dispatch_one -> `lumen-run start` -> fworker), log every decision as one JSONL
line, emit a best-effort Langfuse trace, exit. The dispatcher never claims tickets and
never runs model turns itself; its only side effect is spawning detached fworker units
(plus its own counters in state.json). See DESIGN.md §3 for the pipeline and §9 for the
crash/idempotence story.

CLI:  foreman_dispatcher.py run --once [--dry-run]
      (--once is the only mode — a pass is always one-shot; the flag documents intent
      in the systemd unit. --dry-run logs `would_spawn` and performs zero side effects:
      no spawns, no state writes, no ntfy.)

Decision-log events (one JSON object per line, file + stdout):
  pass_start / pass_end    pass boundaries (pass_start carries config_path + config_sha —
                           the config is read from HEAD of its repo, wq#772, never the raw
                           working-tree file, so this is the only place "what config is
                           live" is answerable from)
  skip                     per-ticket refusal: stage + reason
  spawn / would_spawn      per-ticket dispatch (or dry-run equivalent)
  halt                     pass-level stop: kill_switch, lock_busy, no_slots,
                           spawn_cap, lumen_run_unreachable
  tight_mode               governor pressure flipped the pass into tight-mode
  error                    config/spawn/langfuse/pass failures
  info                     degradations (e.g. langfuse_disabled)
"""

import argparse
import base64
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import router       # noqa: E402
import spawner      # noqa: E402
import concurrency  # noqa: E402

# Env seams (FOREMAN_DISPATCHER_* are new; the rest mirror router/spawner/governor).
# All read at import; tests monkeypatch the module attributes or set env before import.
FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
CONFIG_PATH = os.environ.get("FOREMAN_DISPATCHER_CONFIG",
                             os.path.join(DISPATCHER_DIR, "config.json"))
STATE_PATH = os.environ.get("FOREMAN_DISPATCHER_STATE",
                            os.path.join(FOREMAN_ROOT, "dispatcher", "state.json"))
LOG_PATH = os.environ.get("FOREMAN_DISPATCHER_LOG",
                          os.path.join(FOREMAN_ROOT, "dispatcher.log"))
APPROVALS_DIR = os.environ.get("FOREMAN_APPROVALS_DIR",
                               os.path.join(FOREMAN_ROOT, "approvals"))
DISARM_PATH = os.environ.get("FOREMAN_DISPATCH_DISARM",
                             "/home/lumen/autonomy/DISPATCH_DISARM")
STOP_PATH = os.environ.get("FOREMAN_STOP_FILE", os.path.join(FOREMAN_ROOT, "STOP"))
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
SLOCK_BIN = os.environ.get("FOREMAN_SLOCK_BIN", "/home/lumen/bin/slock")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
GOVERNOR_CLI = os.environ.get("FOREMAN_GOVERNOR_CLI",
                              os.path.join(FOREMAN_ROOT, "governor", "foreman_usaged.py"))
GOVERNOR_STATE = os.environ.get("FOREMAN_STATE_JSON",
                                os.path.join(FOREMAN_ROOT, "state.json"))
LANGFUSE_ENV = os.environ.get("FOREMAN_LANGFUSE_ENV",
                              "/home/lumen/.secrets/langfuse-foreman.env")

SLOCK_SCOPE = "foreman-dispatcher"
SLOCK_TTL_MIN = "30"
FIRE_NTFY_DEDUP_H = 24
FIRE_LANE = "fleet-fires"  # requires_approval is forced in code for this lane (MUST 4)
STAGGER = (spawner.STAGGER_MIN_S, spawner.STAGGER_MAX_S)

FRANK_TITLE_RE = re.compile(r"\[frank gate\]|\[manual\]|owner\s*=\s*frank", re.I)
# wq list line: `[ 12] lane p1 ep0 open     title...` (ep tag only when it differs)
LIST_LINE_RE = re.compile(r"^\[\s*(\d+)\]\s+(\S+)\s+p(\d+)(?:\s+ep(\d+))?\s+(\S+)")


class ConfigError(Exception):
    pass


# ---- config (§4: allowlist, fail-closed on any invalidity) ---------------------------

def _git_show_committed(path):
    """Read `path` as it stands COMMITTED at HEAD of its containing repo (wq#772) — never
    the raw working-tree file. An uncommitted edit or a `git clean` must not change live
    policy. Returns (raw_text, sha). Fails loud (ConfigError); no working-tree fallback."""
    abspath = os.path.abspath(path)
    repo_dir = os.path.dirname(abspath)

    def git(*args):
        try:
            p = subprocess.run(["git", "-C", repo_dir, *args],
                               capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ConfigError(f"config {path}: git {' '.join(args)} failed: {e}")
        if p.returncode != 0:
            raise ConfigError(f"config {path}: git {' '.join(args)} failed: {p.stderr.strip()}")
        return p.stdout

    toplevel = git("rev-parse", "--show-toplevel").strip()
    sha = git("rev-parse", "HEAD").strip()
    relpath = os.path.relpath(abspath, toplevel)
    return git("show", f"HEAD:{relpath}"), sha


def load_config(path=None):
    """Returns (cfg, sha) — sha is the commit the config was read from, for pass_start
    logging (MUST: 'what config is live' answerable from the log alone)."""
    path = path or CONFIG_PATH
    try:
        raw, sha = _git_show_committed(path)
        cfg = json.loads(raw)
    except ValueError as e:
        raise ConfigError(f"config {path}: invalid JSON in committed config: {e}")
    if not isinstance(cfg, dict):
        raise ConfigError(f"config {path}: top level must be an object")

    def pos_int(obj, key, what):
        v = obj.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise ConfigError(f"config {path}: {what}.{key} must be a positive integer")
        return v

    pos_int(cfg, "max_spawns_per_pass", "root")
    pos_int(cfg, "cloud_ceiling", "root")
    tight = cfg.get("tight_mode")
    if not isinstance(tight, dict):
        raise ConfigError(f"config {path}: tight_mode must be an object")
    pos_int(tight, "max_spawns_per_pass", "tight_mode")
    if not isinstance(tight.get("min_pri"), int) or isinstance(tight.get("min_pri"), bool):
        raise ConfigError(f"config {path}: tight_mode.min_pri must be an integer")
    lanes = cfg.get("lanes")
    if not isinstance(lanes, dict):
        raise ConfigError(f"config {path}: lanes must be an object")
    for lane, policy in lanes.items():
        if not isinstance(policy, dict):
            raise ConfigError(f"config {path}: lanes.{lane} must be an object")
        if not isinstance(policy.get("enabled"), bool):
            raise ConfigError(f"config {path}: lanes.{lane}.enabled must be a boolean")
        pos_int(policy, "max_per_day", f"lanes.{lane}")
        if "requires_approval" in policy and not isinstance(policy["requires_approval"], bool):
            raise ConfigError(f"config {path}: lanes.{lane}.requires_approval must be a boolean")
    # MUST 4: a config edit cannot silently disarm fire approval (DESIGN §4, test T4b).
    if FIRE_LANE in lanes:
        lanes[FIRE_LANE]["requires_approval"] = True
    return cfg, sha


# ---- decision log (§8) ---------------------------------------------------------------

class DecisionLog:
    def __init__(self, path, pass_id):
        self.path, self.pass_id, self.events = path, pass_id, []
        self._file_broken = False

    def emit(self, event, **fields):
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "pass_id": self.pass_id, "event": event}
        rec.update(fields)
        self.events.append(rec)
        line = json.dumps(rec)
        print(line, flush=True)  # journald copy
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.path, "a") as f:
                f.write(line + "\n")
        except OSError as e:
            if not self._file_broken:  # journald still has the stream; say so once
                self._file_broken = True
                print(f"[dispatcher] WARN cannot append {self.path}: {e}",
                      file=sys.stderr, flush=True)

    def skip(self, ticket, lane, stage, reason, **fields):
        self.emit("skip", ticket=ticket, lane=lane, stage=stage, reason=reason, **fields)


# ---- state.json (§6.3: per-day lane counters + fire-ntfy dedup ledger) ---------------

def _utcnow():
    return datetime.now(timezone.utc)


def _prune_fire_ntfy(ledger, now):
    out = {}
    for tid, ts in (ledger or {}).items():
        try:
            then = datetime.fromisoformat(ts)
        except (TypeError, ValueError):
            continue
        if now - then < timedelta(hours=FIRE_NTFY_DEDUP_H):
            out[tid] = ts
    return out


def load_state(now=None):
    now = now or _utcnow()
    today = now.strftime("%Y-%m-%d")
    fresh = {"day": today, "lane_counts": {}, "fire_ntfy": {}}
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return fresh
    if not isinstance(st, dict):
        return fresh
    fire = _prune_fire_ntfy(st.get("fire_ntfy"), now)
    if st.get("day") != today:  # UTC day rollover resets counts (§6.3)
        return {"day": today, "lane_counts": {}, "fire_ntfy": fire}
    counts = st.get("lane_counts")
    return {"day": today,
            "lane_counts": counts if isinstance(counts, dict) else {},
            "fire_ntfy": fire}


def save_state(st):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


# ---- kill switches / slock / ntfy ----------------------------------------------------

def kill_switch():
    """Non-None reason string when dispatch must halt (MUST 8). Checked at pass start
    and again between every candidate."""
    if os.path.exists(DISARM_PATH):
        return f"DISPATCH_DISARM present ({DISARM_PATH})"
    if os.path.exists(STOP_PATH):
        return f"foreman STOP present ({STOP_PATH})"
    return None


def slock_acquire():
    holder = f"foreman-dispatcher:{os.getpid()}"
    try:
        p = subprocess.run([SLOCK_BIN, "acquire", SLOCK_SCOPE, "--ttl", SLOCK_TTL_MIN,
                            "--holder", holder], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False, holder  # can't prove single-instance -> behave as lock-busy
    return p.returncode == 0, holder


def slock_release(holder):
    try:
        subprocess.run([SLOCK_BIN, "release", SLOCK_SCOPE, "--holder", holder],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass  # lease TTL expires it


def ntfy(msg, priority="high", title="foreman-dispatcher"):
    try:
        subprocess.run([NTFY_BIN, "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass  # ntfy is telemetry, never load-bearing


# ---- per-ticket gates (§3 stages 3-8) ------------------------------------------------

def gate_met(gate):
    """wq's enforced-precondition semantics (comma list of `path[:exists|missing]`),
    mirrored so a doomed spawn is skipped legibly (DESIGN §5.4). wq re-checks the real
    thing at claim time — this copy is an optimization, not the enforcement point."""
    if not gate or not gate.strip():
        return True, ""
    for clause in [x.strip() for x in gate.split(",") if x.strip()]:
        if ":" in clause:
            path, cond = clause.rsplit(":", 1)
        else:
            path, cond = clause, "exists"
        path, cond = path.strip(), cond.strip().lower()
        present = os.path.exists(path)
        if cond == "exists" and not present:
            return False, f"gate: {path} missing"
        if cond == "missing" and present:
            return False, f"gate: {path} present (must be absent)"
    return True, ""


def frank_gate_reason(t):
    """Non-None detail when the ticket is Frank's, not ours (MUST 3, DESIGN §5).
    Fail-closed: any marker means never claim; there is no override flag."""
    title = t.get("title") or ""
    if FRANK_TITLE_RE.search(title):
        return "title marker ([FRANK GATE]/[manual]/owner=frank)"
    gate_type = t.get("gate_type") or ""
    if gate_type not in ("", "None") and (t.get("frank_response") or "None") in ("", "None"):
        return f"decision gate '{gate_type}' pending (no frank_response)"
    gate = t.get("gate") or ""
    if gate not in ("", "None"):
        ok, why = gate_met(gate)
        if not ok:
            return f"wq gate unmet ({why})"
    return None


def unmet_deps(t, cache):
    """Dep ids not in status=done (MUST 2). A missing dep ticket counts as unmet —
    fail closed. wq claim --id re-checks authoritatively (DESIGN §3 TOCTOU)."""
    deps = t.get("deps") or ""
    if deps in ("", "None"):
        return []
    unmet = []
    for d in [x.strip() for x in deps.split(",") if x.strip()]:
        status = cache.get(d)
        if status is None:
            try:
                status = router.wq_show(d).get("status") or "missing"
            except SystemExit:
                status = "missing"
            cache[d] = status
        if status != "done":
            unmet.append(d)
    return unmet


def candidate_model(t, tid):
    """The model the ticket would dispatch on: its wq tuple, else the router's dry-run
    assignment. None = unroutable (skip; dispatch_cmd would fail the same way)."""
    m = t.get("model")
    if m and m != "None":
        return m
    try:
        d = router.assign(tid, dry_run=True)
    except SystemExit:
        return None
    rung = d.get("rung")
    if not rung or rung.get("model") == "DECOMPOSE":
        return None
    return rung.get("model")


def governor_pressure(model):
    return router.governor_pressure(model, state_path=GOVERNOR_STATE,
                                    governor_cli=GOVERNOR_CLI)


# ---- candidate scan (§3 stage 2: read-only, effective_pri order) ---------------------

def scan_candidates(cfg, log):
    out = []
    for lane, policy in sorted(cfg["lanes"].items()):
        if not policy.get("enabled"):
            continue  # allowlist: disabled lanes are never scanned (§4)
        try:
            p = subprocess.run([WQ_BIN, "list", lane, "--status", "open",
                                "--sort", "effective"],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.emit("error", stage="scan", lane=lane, reason=f"wq list failed: {e}")
            continue
        if p.returncode != 0:
            log.emit("error", stage="scan", lane=lane,
                     reason=f"wq list rc={p.returncode}: {p.stderr.strip()}")
            continue
        for line in p.stdout.splitlines():
            m = LIST_LINE_RE.match(line)
            if not m:
                continue
            tid, tlane, pri, ep, status = m.groups()
            if status != "open" or tlane != lane:
                continue
            out.append({"id": int(tid), "lane": lane, "pri": int(pri),
                        "epri": int(ep) if ep is not None else int(pri)})
    out.sort(key=lambda c: (c["epri"], c["id"]))  # wq#661 aging respected across lanes
    return out


# ---- fire-ticket approval (§5, MUST 4) -----------------------------------------------

def fire_approval_path(tid):
    return os.path.join(APPROVALS_DIR, f"wq{tid}.approved")


def handle_unapproved_fire(state, tid, lane, dry_run, log):
    log.skip(tid, lane, "fire_approval", "fire_unapproved",
             detail=f"{fire_approval_path(tid)} absent")
    now = _utcnow()
    last = state["fire_ntfy"].get(str(tid))
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=FIRE_NTFY_DEDUP_H):
                return  # already pinged Frank within 24 h — never flood (§5)
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason="fire_unapproved")
        return
    ntfy(f"wq#{tid} ({lane}) is dispatch-ready but has no approval file. "
         f"To approve: touch {fire_approval_path(tid)}", priority="default",
         title="fire ticket awaiting approval")
    state["fire_ntfy"][str(tid)] = now.isoformat(timespec="seconds")
    save_state(state)


# ---- Langfuse trace (§8: best-effort by contract) ------------------------------------

def _load_langfuse_creds(path):
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            k, _, v = line.partition("=")
            creds[k.strip()] = v.strip().strip('"').strip("'")
    return creds


def emit_langfuse(pass_id, events):
    """One trace per pass, one span per ticket decision. Returns None when disabled
    (no keys — log-only degradation), else the number of events shipped. Any exception
    propagates; the caller logs `langfuse_emit_failed` and moves on — this function is
    never load-bearing (DESIGN §8)."""
    if not os.path.exists(LANGFUSE_ENV):
        return None
    creds = _load_langfuse_creds(LANGFUSE_ENV)
    host = creds.get("LANGFUSE_HOST")
    pk, sk = creds.get("LANGFUSE_PUBLIC_KEY"), creds.get("LANGFUSE_SECRET_KEY")
    if not (host and pk and sk):
        return None
    now = datetime.now(timezone.utc).isoformat()
    summary = {"decisions": len(events)}
    batch = [{"id": str(uuid.uuid4()), "type": "trace-create", "timestamp": now,
              "body": {"id": pass_id, "name": "foreman-dispatch", "timestamp": now,
                       "metadata": summary, "tags": ["dispatcher"]}}]
    for ev in events:
        if ev.get("ticket") is None:
            continue
        batch.append({"id": str(uuid.uuid4()), "type": "span-create", "timestamp": now,
                      "body": {"id": str(uuid.uuid4()), "traceId": pass_id,
                               "name": f"wq{ev['ticket']}:{ev['event']}",
                               "startTime": ev.get("ts"), "metadata": ev}})
    req = urllib.request.Request(
        host.rstrip("/") + "/api/public/ingestion",
        data=json.dumps({"batch": batch}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(
                     f"{pk}:{sk}".encode()).decode()},
        method="POST")
    with urllib.request.urlopen(req, timeout=5):
        pass
    return len(batch)


LANGFUSE_EMIT = emit_langfuse  # seam: tests replace to prove best-effort isolation


# ---- the pass (§3) -------------------------------------------------------------------

def _do_pass(cfg, log, state, dry_run):
    candidates = scan_candidates(cfg, log)
    tight_cfg = cfg["tight_mode"]
    tight = False
    spawned = 0
    dep_cache, pressure_cache = {}, {}

    for i, cand in enumerate(candidates):
        tid, lane = cand["id"], cand["lane"]

        # stage 0 recheck — a mid-run DISARM/STOP halts before the next spawn (MUST 8)
        ks = kill_switch()
        if ks:
            log.emit("halt", stage="kill_switch", reason=ks)
            break
        limit = tight_cfg["max_spawns_per_pass"] if tight else cfg["max_spawns_per_pass"]
        if spawned >= limit:
            log.emit("halt", stage="spawn_cap",
                     reason=f"per-pass spawn cap reached ({spawned}/{limit}"
                            f"{', tight' if tight else ''})")
            break

        # stage 3 — lane policy (MUST 5 lane caps; wq#713 repo mapping, MUST 7)
        policy = cfg["lanes"][lane]
        if state["lane_counts"].get(lane, 0) >= policy["max_per_day"]:
            log.skip(tid, lane, "lane_policy", "lane_cap",
                     detail=f"max_per_day={policy['max_per_day']} reached")
            continue
        repo = router.repo_for_lane(lane)
        if not repo:
            log.skip(tid, lane, "lane_policy", "lane_unmapped",
                     detail="no LANE_REPO entry — never guess a repo")
            continue

        try:
            t = router.wq_show(tid)
        except SystemExit:
            log.skip(tid, lane, "scan", "vanished")
            continue
        if t.get("status") != "open":
            log.skip(tid, lane, "scan", "not_open", detail=t.get("status"))
            continue

        # stage 4 — Frank-gate (MUST 3): never claimed, no override flag
        why = frank_gate_reason(t)
        if why:
            log.skip(tid, lane, "frank_gate", "frank_gated", detail=why)
            continue

        # stage 5 — fire approval (MUST 4): forced for fleet-fires regardless of config
        if policy.get("requires_approval") or lane == FIRE_LANE:
            if not os.path.exists(fire_approval_path(tid)):
                handle_unapproved_fire(state, tid, lane, dry_run, log)
                continue

        # stage 6 — deps (MUST 2)
        unmet = unmet_deps(t, dep_cache)
        if unmet:
            log.skip(tid, lane, "deps", "deps_unmet", detail=",".join(unmet))
            continue

        # stages 7+9 share one live-unit read (MUST 6): units are the ground truth
        try:
            units = concurrency.list_running_units()
        except concurrency.LumenRunError as e:
            log.emit("halt", stage="concurrency", reason="lumen_run_unreachable",
                     detail=str(e))  # fail toward under-subscription
            break
        if f"fworker-wq{tid}" in units:
            log.skip(tid, lane, "already_running", "already_running")
            continue

        # stage 8 — budget (MUST 5): governor verdict + floors via router
        model = candidate_model(t, tid)
        if not model:
            log.skip(tid, lane, "budget", "route_unassigned",
                     detail="router has no rung for this ticket")
            continue
        pressure = pressure_cache.get(model)
        if pressure is None:
            pressure = governor_pressure(model)
            pressure_cache[model] = pressure
        if pressure == "deny":
            log.skip(tid, lane, "budget", "governor_deny", model=model)
            continue
        if pressure == "tight" and not tight:
            tight = True
            log.emit("tight_mode", model=model,
                     reason=f"pressure tight: max {tight_cfg['max_spawns_per_pass']} "
                            f"spawn(s), pri<={tight_cfg['min_pri']} only")
        if tight:
            if spawned >= tight_cfg["max_spawns_per_pass"]:
                log.emit("halt", stage="spawn_cap",
                         reason=f"tight-mode spawn cap reached ({spawned})")
                break
            if cand["pri"] > tight_cfg["min_pri"]:
                log.skip(tid, lane, "budget", "tight_pri", model=model,
                         detail=f"pri {cand['pri']} > tight min_pri {tight_cfg['min_pri']}")
                continue

        # stage 9 — concurrency ceiling, local rung excluded (T4)
        cloud = [u for u in units if not concurrency.is_local_unit(u)]
        if len(cloud) >= cfg["cloud_ceiling"]:
            log.emit("halt", stage="concurrency", reason="no_slots",
                     detail=f"{len(cloud)} cloud unit(s) at ceiling "
                            f"{cfg['cloud_ceiling']}; next tick retries")
            break

        # stage 10 — spawn via the existing stack (MUST 7: router owns repo + tuple)
        if dry_run:
            spawned += 1  # count so dry-run reports caps honestly
            log.emit("would_spawn", ticket=tid, lane=lane, model=model, repo=repo)
            continue
        unit = spawner.dispatch_one(tid, repo)
        if unit is None:
            log.emit("error", stage="spawn", ticket=tid, lane=lane,
                     reason="spawn_failed", model=model)
            ntfy(f"dispatcher: spawn failed for wq#{tid} ({lane}) — see dispatcher.log")
            continue
        spawned += 1
        state["lane_counts"][lane] = state["lane_counts"].get(lane, 0) + 1
        save_state(state)  # §9: written after each spawn, atomically
        log.emit("spawn", ticket=tid, lane=lane, model=model, repo=repo, unit=unit)
        if i < len(candidates) - 1:
            time.sleep(random.uniform(*STAGGER))  # same stagger doctrine as spawner

    return {"candidates": len(candidates), "spawned": spawned, "tight": tight}


def run_pass(dry_run=False):
    pass_id = ("fdisp-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
               + f"-{os.getpid()}")
    log = DecisionLog(LOG_PATH, pass_id)

    ks = kill_switch()  # stage 0 (MUST 8)
    if ks:
        log.emit("halt", stage="kill_switch", reason=ks)
        return 0
    try:
        cfg, config_sha = load_config()  # §4: invalid/uncommitted config fails the pass closed
    except ConfigError as e:
        log.emit("error", stage="config", reason=str(e))
        ntfy(f"dispatcher: invalid config — pass aborted: {e}")
        return 1
    ok, holder = slock_acquire()  # stage 1
    if not ok:
        log.emit("halt", stage="lock", reason="lock_busy")
        return 0

    rc = 0
    try:
        log.emit("pass_start", dry_run=dry_run, config_path=CONFIG_PATH, config_sha=config_sha)
        summary = _do_pass(cfg, log, load_state(), dry_run)
        log.emit("pass_end", **summary)
    except Exception as e:  # noqa: BLE001 — pass boundary: log + ntfy, never silent
        log.emit("error", stage="pass", reason=f"unhandled: {e!r}")
        ntfy(f"dispatcher: pass {pass_id} crashed: {e!r}")
        rc = 1
    finally:
        slock_release(holder)
        try:
            shipped = LANGFUSE_EMIT(pass_id, log.events)
            if shipped is None:
                log.emit("info", stage="langfuse", reason="langfuse_disabled",
                         detail="no keys at LANGFUSE_ENV — log-only degradation")
        except Exception as e:  # noqa: BLE001 — best-effort by contract (§8)
            log.emit("error", stage="langfuse", reason="langfuse_emit_failed",
                     detail=repr(e))
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="foreman auto-dispatcher (wq#715 AUTO-3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one dispatch pass and exit")
    r.add_argument("--once", action="store_true",
                   help="documentational: a pass is always one-shot")
    r.add_argument("--dry-run", action="store_true",
                   help="decide + log would_spawn; zero side effects")
    a = ap.parse_args(argv)
    return run_pass(dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
