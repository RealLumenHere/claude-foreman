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
  skip                     per-ticket refusal: stage + reason (incl. runaway_stuck —
                           wq#718's runaway.py; see stuck_ticket_ids() — and spawn_loop,
                           wq#1703's spawn_loop_guard.looping())
  aq_row                   an alert-queue row this pass filed (wq#1703 spawn loops)
  spawn / would_spawn      per-ticket dispatch (or dry-run equivalent)
  halt                     pass-level stop: kill_switch, circuit_breaker (wq#718 —
                           see circuit_breaker.py; disarms before this halt is even
                           logged), lock_busy, no_slots, spawn_cap,
                           lumen_run_unreachable
  capability_lint          per-candidate capability derivation (wq#1437 slice B,
                           BROKER_WIRING.md §2.1): tags/unresolved/refused, emitted
                           only when the derivation is non-empty (pass_end's
                           lint_scanned/lint_flagged carry the totals).
                           capability_lint_error is the swallowed-exception twin.
                           FOREMAN_NEEDS_LINT_MODE=off silences both.
  capability_applied       stage 3.7 wrote derived tags into the ticket's `needs`
                           column (wq#1438 slice C, §2.1 auto-tag). The four capability
                           SKIP reasons it can raise instead are cited_path_unreachable,
                           node_unknown, capability_denied_by_policy and
                           needs_unresolved. FOREMAN_NEEDS_LINT_MODE=warn is
                           the rollback: derive and log, decide nothing.
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
import signal
import sqlite3
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
import router          # noqa: E402
import spawner         # noqa: E402
import concurrency     # noqa: E402
import pacing          # noqa: E402
import circuit_breaker  # noqa: E402
import local_capacity   # noqa: E402  # wq#2130: per-backend local slots + local pause
import cli_canary       # noqa: E402  # wq#2527: installed-CLI canary hold
import runaway          # noqa: E402
import self_heal        # noqa: E402  # charge_mode_legacy(): FOREMAN_CHARGE_MODE rollback lever
import spawn_loop_guard  # noqa: E402  # wq#1703: restart-loop breaker
import psi_pressure     # noqa: E402  # wq#844
import cpu_pressure     # noqa: E402  # wq#2631/wq#2647
import burn_rate        # noqa: E402  # wq#1669: burn-rate spawn controller
import harness_preflight  # noqa: E402  # wq#1259 §2c dispatch gate
import vlogs_mirror      # noqa: E402  # wq#1416
import fire_gate          # noqa: E402  # wq#1261 §4 item 1: no-probe fire refusal
import capability_lint    # noqa: E402  # wq#1437 BROKER_WIRING §2.1 slice B (warn)
import capability         # noqa: E402  # wq#1438 BROKER_WIRING §2.2 slice C (pre-skip)
import capability_proposal  # noqa: E402  # wq#1697: one keyed aq grant proposal per path
import liveness_clear     # noqa: E402  # wq#1478 LIVENESS_GATE §12.4: stale Frank-gates auto-clear
import gates              # noqa: E402  # DISPATCH_REBUILD step 8: the ordered gate objects
import gate_store         # noqa: E402  # DISPATCH_REBUILD step 11: dispatch_gate parking table
import decomposer         # noqa: E402  # wq#2516: DECOMPOSE-rung consumer's guards (marker, depth)

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
# wq#1372: a stale preflight stamp is auto-reproved inline (harness_preflight.preflight
# spawns a real throwaway `claude -p` live-proof session, not free) rather than left to
# strand the lane until a human notices. Bound retry cadence so a persistently broken
# profile can't re-spawn that session every dispatch pass forever.
PREFLIGHT_REPROVE_COOLDOWN_H = 1
# wq#1409 §2.2: a reprove that ERRORED (claude spawn failed, hung, exited non-zero) is
# transient until proven otherwise — retry inside the pass with this backoff before
# paging; a reprove that FAILED (static lint / live denials) is the profile being
# broken and gets no retry (retrying the same proof cannot change the answer).
REPROVE_RETRY_BACKOFF_S = (0, 30, 120)
# The whole ladder must fit under the unit's TimeoutStartSec=600 with headroom for the
# rest of the pass; a live proof is capped at harness_preflight.LIVE_PROOF_TIMEOUT.
REPROVE_LADDER_BUDGET_S = 420
_sleep = time.sleep  # monkeypatch seam for tests
FIRE_LANE = "fleet-fires"  # kept for the disarmed fallback noted at line ~196 if
# requires_approval forcing is ever restored; no longer used to force the gate itself
# (MUST 4 hardcode removed 2026-08-23 per Frank directive, see load_config below)
STAGGER = (spawner.STAGGER_MIN_S, spawner.STAGGER_MAX_S)
# wq#1094: the only lane where an unmapped LANE_REPO isn't final — repo-sweep tickets
# in 'auto' carry their own repo in the ticket (repo_sweep.repo_hint). Every other
# unmapped lane still short-circuits before paying for a wq_show (unchanged).
REPO_HINT_LANE = "auto"

# 'user' is Frank's action/decision queue — physical work, purchases, reboots, repo-disposition
# calls. Human-owned, NEVER worker-dispatchable. Step 8: scan_candidates no longer emits them at
# all; the HumanLane gate stays as the second wall and skips as frank_gated (never lane_unmapped,
# which looked like a routing defect and buried 53 of Frank's real to-dos in noise).
HUMAN_LANES = {"user", "user-projects", "frank-re", "frank-hw", "frank-funding", "frank-cred"}

# DISPATCH_REBUILD step 8: `dispatchable` used to be candidates minus an allowlist of skip
# reasons (wq#1410 STRUCTURAL_SKIP_REASONS), so every new gate reason read as a stall until
# someone remembered to add it (harness_defect_hold was the fourth recurrence, F7). It is
# now the READY set by construction: len(ready[pool]) - lane_capped[pool], see _do_pass.

# FOREMAN_DISPATCH_HYGIENE=legacy restores the pre-step-8 pass shape: dry-run takes the
# lock, the stale-preflight reprove runs inside it, human-lane tickets are gate candidates.
HYGIENE_ENV = "FOREMAN_DISPATCH_HYGIENE"


def hygiene_legacy():
    return os.environ.get(HYGIENE_ENV) == "legacy"


FRANK_TITLE_RE = re.compile(r"\[frank gate\]|\[manual\]|owner\s*=\s*frank", re.I)
# wq#428: an epic parent is a rollup over children, not work — it carries no
# acceptance criteria a build lane can satisfy, so every lane that claimed one came
# back with an empty diff and was judged FALSE_COMPLETE (three lanes burnt that way
# before this gate). The marker was written in the title all along; nothing read it.
NODISPATCH_TITLE_RE = re.compile(r"\[epic\]|\[do[ -]?not[ -]?claim\]", re.I)
# wq#2782 (ISSUE-LOG 850 rule c): Frank's 2026-09-25 hold on ALL CF-resource rotation —
# the mcpw/Commander outage traced back to an unattended CF token rotation that deleted
# every Cloudflare Access app on the account. `rotation_title()` (harness/fworker.py)
# always puts the tripwire's mask label(s) in parens at the end of a `[rotation]` row's
# title (and the marker mid-title on the `[verify]` row it spawns), so a row whose tag
# names cloudflare is caught by title alone. The wq#2275 tripwire now files these
# `--frank-gated` (fworker.mask_alarm/is_cf_rotation), which the TicketLoad gate above
# already refuses (status != "open") — this is the second wall, for a row that predates
# that fix (wq#2738) or that some other path re-opens. No override: lifting the hold
# means deleting this check once ISSUE-LOG 850 is closed.
CF_ROTATION_HOLD_RE = re.compile(r"\[rotation\].*\bcloudflare\b", re.I)
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


def config_blob_sha(path=None):
    """Blob sha of the config as COMMITTED at HEAD (`git rev-parse HEAD:<path>`) — changes
    only when the config's CONTENT changes, unlike the commit sha, which moves on every
    unrelated merge to the repo. wq#2127 step 12: this is the gate-parking salt. Salting
    the park fingerprints with the commit sha meant every foreman merge (several an hour)
    unparked every parked ticket and re-ran the whole gate pipeline over them — the step-11
    parking then only held between merges, and `gate_unparked why=fingerprint_changed`
    fired for all ~40 parked tickets per merge (10k+ events in a week against 258 parks).
    Returns "" (no salt) when git cannot answer, matching the pre-fix empty default."""
    path = path or CONFIG_PATH
    abspath = os.path.abspath(path)
    repo_dir = os.path.dirname(abspath)
    try:
        toplevel = subprocess.run(["git", "-C", repo_dir, "rev-parse", "--show-toplevel"],
                                  capture_output=True, text=True, timeout=10)
        if toplevel.returncode != 0:
            return ""
        relpath = os.path.relpath(abspath, toplevel.stdout.strip())
        p = subprocess.run(["git", "-C", repo_dir, "rev-parse", f"HEAD:{relpath}"],
                           capture_output=True, text=True, timeout=10)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def load_config(path=None):
    """Returns (cfg, lane_warnings, sha) — sha is the commit the config was read from
    (wq#772: pass_start logging, 'what config is live' answerable from the log alone)."""
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
    # wq#2426: cloud_ceiling 0 is a legal "cloud partition off" switch (the shape of
    # Frank's 2026-09-21 stopgap, which this validator used to refuse outright).
    ceiling = cfg.get("cloud_ceiling")
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 0:
        raise ConfigError(f"config {path}: root.cloud_ceiling must be a non-negative integer")
    for key in ("local_ceiling", "local_max_spawns_per_pass", "pass_deadline_s"):
        if key in cfg:
            pos_int(cfg, key, "root")
    tight = cfg.get("tight_mode")
    if not isinstance(tight, dict):
        raise ConfigError(f"config {path}: tight_mode must be an object")
    pos_int(tight, "max_spawns_per_pass", "tight_mode")
    # wq#2516: the decomposer block is optional (absent = off), but a present block must
    # be well-formed — a typo'd switch must fail loud, never silently read as off/on.
    dec = cfg.get("decomposer")
    if dec is not None:
        if not isinstance(dec, dict):
            raise ConfigError(f"config {path}: decomposer must be an object")
        if not isinstance(dec.get("enabled"), bool):
            raise ConfigError(f"config {path}: decomposer.enabled must be a boolean")
        if "max_per_pass" in dec:
            pos_int(dec, "max_per_pass", "decomposer")
    if not isinstance(tight.get("min_pri"), int) or isinstance(tight.get("min_pri"), bool):
        raise ConfigError(f"config {path}: tight_mode.min_pri must be an integer")
    lanes = cfg.get("lanes")
    if not isinstance(lanes, dict):
        raise ConfigError(f"config {path}: lanes must be an object")
    # wq#824: per-lane validation is fail-SOFT. A bad lane is disabled + warned,
    # never halts all dispatch. Only structural (top-level) errors are fail-closed.
    lane_warnings = []
    valid_lanes = {}
    for lane, policy in lanes.items():
        if not isinstance(policy, dict):
            lane_warnings.append(f"lanes.{lane} must be an object — lane disabled")
            valid_lanes[lane] = {"enabled": False, "_config_error": True}
            continue
        if not isinstance(policy.get("enabled"), bool):
            lane_warnings.append(f"lanes.{lane}.enabled must be a boolean — lane disabled")
            valid_lanes[lane] = {"enabled": False, "_config_error": True}
            continue
        if not policy["enabled"]:
            # Disabled lane: skip field validation entirely (wq#824 root cause fix).
            # enabled:false alone is sufficient to disable a lane.
            valid_lanes[lane] = policy
            continue
        # Enabled lane: validate operational fields
        v = policy.get("max_per_day")
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            lane_warnings.append(
                f"lanes.{lane}.max_per_day must be a positive integer "
                f"(got {v!r}) — lane disabled")
            policy = dict(policy, enabled=False, _config_error=True)
            valid_lanes[lane] = policy
            continue
        if "requires_approval" in policy and not isinstance(policy["requires_approval"], bool):
            lane_warnings.append(
                f"lanes.{lane}.requires_approval must be a boolean — field ignored")
            policy = dict(policy)
            del policy["requires_approval"]
        valid_lanes[lane] = policy
    cfg["lanes"] = valid_lanes
    # MUST 4 hardcode REMOVED 2026-08-23 per Frank directive: fleet-fires
    # requires_approval:false is now honored from config.json. The old hardcode
    # forced requires_approval=True regardless of config, silently ignoring Frank's
    # intentional disarm. Safety note: if re-enabling, also restore the
    # `or lane == FIRE_LANE` fallback in stage 5 below.
    return cfg, lane_warnings, sha


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


# ---- state.json (§6.3: per-day lane counters + fire-ntfy/pacing-ntfy dedup ledgers) --

PACING_NTFY_PRUNE_H = 72  # generous vs. the configurable ntfy_dedup_hours (default 6):
                          # this only bounds ledger growth, not the re-notify cadence.


def _utcnow():
    return datetime.now(timezone.utc)


def _ledger_ts(entry):
    """A dedup-ledger value is an iso timestamp string, or (preflight_ntfy since
    wq#1409) a dict {ts, attempts, last_error, paged} whose `ts` is that string."""
    if isinstance(entry, dict):
        entry = entry.get("ts")
    return datetime.fromisoformat(entry)


def _prune_dedup(ledger, now, hours):
    """Shared dedup-ledger pruning for fire_ntfy (§5), pacing_ntfy (AUTO-4), and
    runaway_ntfy (wq#718) — same {key: iso_ts} shape, caller picks the window."""
    out = {}
    for key, ts in (ledger or {}).items():
        try:
            then = _ledger_ts(ts)
        except (TypeError, ValueError, AttributeError):
            continue
        if now - then < timedelta(hours=hours):
            out[key] = ts
    return out


def load_state(now=None):
    now = now or _utcnow()
    today = now.strftime("%Y-%m-%d")
    fresh = {"day": today, "lane_counts": {}, "fire_ntfy": {}, "pacing_ntfy": {},
             "runaway_ntfy": {}, "preflight_ntfy": {}, "gate_error_ntfy": {},
             "harness_defect_ntfy": {}, "capability_ntfy": {}, "spawn_loop_ntfy": {},
             "grant_proposals": {}, "spawn_ledger": {}, "spawn_loop_trips": {},
             "harness_defect_seen": {}}
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return fresh
    if not isinstance(st, dict):
        return fresh
    fire = _prune_dedup(st.get("fire_ntfy"), now, FIRE_NTFY_DEDUP_H)
    pacing_ntfy = _prune_dedup(st.get("pacing_ntfy"), now, PACING_NTFY_PRUNE_H)
    runaway_ntfy = _prune_dedup(st.get("runaway_ntfy"), now, FIRE_NTFY_DEDUP_H)
    preflight_ntfy = _prune_dedup(st.get("preflight_ntfy"), now, FIRE_NTFY_DEDUP_H)
    gate_error_ntfy = _prune_dedup(st.get("gate_error_ntfy"), now, FIRE_NTFY_DEDUP_H)
    harness_defect_ntfy = _prune_dedup(st.get("harness_defect_ntfy"), now, FIRE_NTFY_DEDUP_H)
    capability_ntfy = _prune_dedup(st.get("capability_ntfy"), now, FIRE_NTFY_DEDUP_H)
    spawn_loop_ntfy = _prune_dedup(st.get("spawn_loop_ntfy"), now, FIRE_NTFY_DEDUP_H)
    grant_proposals = _prune_dedup(st.get("grant_proposals"), now, FIRE_NTFY_DEDUP_H)
    # wq#1703: {tid: [{ts, attempts}]} — a list per ticket, not a single timestamp, so it
    # prunes on its own window rather than through _prune_dedup.
    spawn_ledger = spawn_loop_guard.prune_ledger(st.get("spawn_ledger"), now)
    # step 8: {tid: [iso ts]} loop episodes on a 24 h window; {tid: {fp, mtime_ns}} is the
    # harness-defect edit witness (runaway.harness_defect_ticket_ids prunes its own keys).
    spawn_loop_trips = spawn_loop_guard.prune_trips(st.get("spawn_loop_trips"), now)
    seen = st.get("harness_defect_seen")
    counts = st.get("lane_counts")
    # UTC day rollover resets lane counts, not the ledgers (§6.3)
    if st.get("day") != today or not isinstance(counts, dict):
        counts = {}
    return {"day": today, "lane_counts": counts, "fire_ntfy": fire,
            "pacing_ntfy": pacing_ntfy, "runaway_ntfy": runaway_ntfy,
            "preflight_ntfy": preflight_ntfy, "gate_error_ntfy": gate_error_ntfy,
            "harness_defect_ntfy": harness_defect_ntfy,
            "capability_ntfy": capability_ntfy, "spawn_loop_ntfy": spawn_loop_ntfy,
            "grant_proposals": grant_proposals,
            "spawn_ledger": spawn_ledger, "spawn_loop_trips": spawn_loop_trips,
            "harness_defect_seen": seen if isinstance(seen, dict) else {}}


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


_DEAD_HOLDER_RE = re.compile(r"held by (foreman-dispatcher:(\d+))\b")


def _pass_pid_alive(pid):
    """True while `pid` is a live foreman_dispatcher process. A recycled PID (alive but
    running something else) counts as dead: the pass that took the lock is gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"foreman_dispatcher" in f.read()
    except OSError:
        return True  # can't read it -> assume alive rather than steal from a live pass


def _slock_run(action, holder):
    argv = [SLOCK_BIN, action, SLOCK_SCOPE, "--holder", holder]
    if action == "acquire":
        argv += ["--ttl", SLOCK_TTL_MIN]
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def slock_acquire():
    """wq#2080: a DENIED whose holder is a dead foreman-dispatcher pass is a leaked lock
    (SIGTERM at TimeoutStartSec skips `finally`), not a live rival — steal it and retry
    once so one crashed pass can't wedge every later pass until the 30m TTL."""
    holder = f"foreman-dispatcher:{os.getpid()}"
    try:
        p = _slock_run("acquire", holder)
        m = _DEAD_HOLDER_RE.search(p.stdout) if p.returncode else None
        if m and int(m.group(2)) != os.getpid() and not _pass_pid_alive(int(m.group(2))):
            _slock_run("release", m.group(1))
            p = _slock_run("acquire", holder)
    except (OSError, subprocess.TimeoutExpired):
        slock_release(holder)  # a timed-out acquire may still have been granted
        return False, holder  # can't prove single-instance -> behave as lock-busy
    except BaseException:  # SIGTERM -> SystemExit mid-acquire (see _term_to_exit)
        slock_release(holder)
        raise
    return p.returncode == 0, holder


def slock_release(holder):
    try:
        subprocess.run([SLOCK_BIN, "release", SLOCK_SCOPE, "--holder", holder],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass  # lease TTL expires it


def ntfy(msg, priority="high", title="foreman-dispatcher"):
    vlogs_mirror.mirror("foreman_dispatcher", title, priority, msg)
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
    if CF_ROTATION_HOLD_RE.search(title):
        return "CF-resource rotation hold (ISSUE-LOG 850 rule c, Frank 2026-09-25)"
    gate_type = t.get("gate_type") or ""
    if gate_type not in ("", "None") and (t.get("frank_response") or "None") in ("", "None"):
        return f"decision gate '{gate_type}' pending (no frank_response)"
    gate = t.get("gate") or ""
    if gate not in ("", "None"):
        ok, why = gate_met(gate)
        if not ok:
            return f"wq gate unmet ({why})"
    return None


def _liveness_autoclosed(tid, t, log):
    """wq#1478: True when the liveness gate closed this Frank-gated ticket just now.
    Fail-open: a liveness exception is logged and the caller proceeds to its normal
    frank_gated skip — the gate can only ever remove a ticket from Frank's queue."""
    try:
        return liveness_clear.dispatch_hook(tid, t, router.wq_show, log.emit)
    except Exception as e:  # noqa: BLE001
        log.emit("error", stage="liveness", ticket=tid, reason=f"liveness hook: {e!r}")
        return False


def nodispatch_reason(t):
    """Non-None detail when the ticket is a tracker, not work (wq#428).
    Fail-closed like frank_gate_reason: the marker is the whole rule, no override."""
    if NODISPATCH_TITLE_RE.search(t.get("title") or ""):
        return "title marker ([EPIC]/[DO NOT CLAIM])"
    return None


def hold_reason(t):
    """wq#1461: non-None detail when a `wq hold` on this ticket is still live. BUILD
    class (third instance: wq#1410 a1/a2, wq#1409 09-07 09:37, wq#1411 orch vs
    fworker 09-07 10:49) — an orchestrator directed to build a ticket whose deps are
    unmet cannot `claim --id` it (wq#582, no --force) and had no other way to record
    intent, so the moment the dep merged the dispatcher spawned an fworker on the SAME
    ticket the orchestrator was already mid-build on. `wq hold <id> --by <agent> --ttl
    N` fixes that: the hold is checked here, BEFORE the deps stage, so it names the
    holder even while the ticket is still deps-blocked (the DecisionLog line is the
    signal that intent is already recorded, not just that dispatch would fail anyway).
    `t["hold_by"]`/`t["hold_expires"]` come straight off `wq show`'s SELECT * — a
    plain-text 'None' means unset (wq#1277 stringifies every NULL column).
    Rollback: FOREMAN_IGNORE_HOLDS=1 makes the dispatcher blind to holds entirely."""
    if os.environ.get("FOREMAN_IGNORE_HOLDS") == "1":
        return None
    hold_by = t.get("hold_by") or ""
    hold_expires = t.get("hold_expires") or ""
    if hold_by in ("", "None") or hold_expires in ("", "None"):
        return None
    if hold_expires < _utcnow().strftime("%Y-%m-%d %H:%M:%S"):
        return None  # expired — same lexical-compare idiom wq itself uses on hold_expires
    return f"held by '{hold_by}' until {hold_expires}Z"


DEP_UNREADABLE = "unreadable"  # dep_cache status of a dep whose `wq show` failed this pass


def unmet_deps(t, cache):
    """Dep ids not in status=done (MUST 2). A missing dep ticket counts as unmet —
    fail closed. wq claim --id re-checks authoritatively (DESIGN §3 TOCTOU). A dep whose
    `wq show` failed is unmet too, cached as DEP_UNREADABLE so the Deps gate can tell that
    verdict is a transient read failure (never parked) from a real status."""
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
                if _terminating:
                    raise
                status = DEP_UNREADABLE
            cache[d] = status
        if status != "done":
            unmet.append(d)
    return unmet


ROUTE_UNREADABLE = "route_unreadable"  # candidate_model skip reason: router.assign raised SystemExit
CLI_CANARY_HELD = "cli_canary_held"    # wq#2527: the ticket's model fails the installed-CLI canary
ROUTE_DECOMPOSE = "route_decompose"    # rung.model == DECOMPOSE — wq#2516 sends it to the decomposer
                                       # when config `decomposer.enabled`; off, it skips exactly as
                                       # the old route_unassigned did (the DECOMPOSE rung had no
                                       # consumer at all before wq#2515/wq#2516)


def candidate_model(t, tid):
    """The model the ticket would dispatch on: its wq tuple, else the router's dry-run
    assignment. Returns (model, skip_reason); skip_reason is None iff model is set.

    wq#1368: wq#1358/#1359 carried archetype='build' (a LANE name, not an archetype)
    straight onto a ticket. router.assign -> select -> candidates -> manifest.chain()
    raises router.ManifestError('no routing row for archetype ...') for that shape, and
    it used to propagate uncaught out of this function, out of _do_pass's per-ticket
    loop entirely, crashing the WHOLE pass (run_pass's try/except Exception around
    _do_pass caught it, but only after every other ready candidate in this pass had
    already been abandoned mid-loop). A bad archetype on one ticket must only ever cost
    that one ticket a skip, never the pass."""
    m = t.get("model")
    if m and m != "None":
        # wq#2527: a tuple written before its model's canary failed must wait, not launch into
        # claude_code_version_too_old. assign() below already routes around a held rung.
        if cli_canary.held(m, router.load_manifest().models):
            return None, CLI_CANARY_HELD
        return m, None
    try:
        d = router.assign(tid, dry_run=True)
    except SystemExit:
        if _terminating:
            raise
        return None, ROUTE_UNREADABLE  # router.wq_show failed: not a verdict on the ticket
    except router.ManifestError as e:
        ntfy(f"dispatcher: wq#{tid} has an unroutable archetype ({e}) — skipped "
             f"every pass until fixed", priority="high", title="dispatcher bad archetype")
        return None, "bad_archetype"
    rung = d.get("rung")
    if not rung:
        if any(cli_canary.HOLD_TAG in str(r) for r in d.get("reasons") or []):
            return None, CLI_CANARY_HELD   # not a verdict on the ticket: never park it
        return None, "route_unassigned"
    if rung.get("model") == "DECOMPOSE":
        return None, ROUTE_DECOMPOSE
    return rung.get("model"), None


def decomposer_cfg(cfg):
    block = cfg.get("decomposer")
    return block if isinstance(block, dict) else {}


def decomposer_enabled(cfg):
    """wq#2516: the DECOMPOSE rung's consumer switch. OFF BY DEFAULT and absent-means-off:
    flipping it is a deliberate reviewed config commit (load_config reads HEAD, wq#772),
    never a code deploy side effect."""
    return decomposer_cfg(cfg).get("enabled") is True


def _decompose_children(tid):
    """(id, status) of tickets carrying this parent's `Decomposed-from:` marker
    (decomposer.py's own idempotency key), read-only from queue.db — the gates.py
    `_live_holds` read pattern. Status rides along so dispatch_decomposer can tell
    "already decomposed, children in flight" from "all children done, close the parent"
    (wq#2517). Fail-CLOSED (None) on an unreadable db: a decomposer spawned blind could
    re-decompose a parent whose children it simply could not see (the wq#1141
    duplicate-worker collision)."""
    path = os.environ.get("WQ_DB", gates.WQ_DB_DEFAULT)
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute("SELECT id, status FROM tasks WHERE detail LIKE ? ORDER BY id",
                               (f"%{decomposer.MARK_FROM}{int(tid)}\n%",)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return [(r[0], str(r[1])) for r in rows]


def dispatch_decomposer(ctx, tid, t):
    """wq#2516 phase 2: consume the DECOMPOSE rung — spawn decompose_planner.py (wq#2515) as
    a detached `decomposer-wq<tid>` unit instead of skipping the ticket as route_unassigned.
    Only the Route gate calls this, and only when `decomposer.enabled` is on.

    Every return is a gates.Fail: the parent never proceeds toward a worker spawn. On a
    successful spawn the unit files the children and links them as the parent's deps, so
    the Deps gate holds the parent from the next pass; once every child is done the deps
    clear, the parent lands back here and close_decomposed_parent() ends it (wq#2517).
    Guards fail closed and (except depth) never park — children, live units and the
    per-pass cap are live external state. decompose() re-checks children/depth itself;
    the dispatcher-side checks exist so an already-decomposed parent never even costs a
    spawn (acceptance: never dispatched for a ticket that already has children)."""
    lane = t.get("lane", "")
    if decomposer.parse_depth(t.get("detail")) >= decomposer.MAX_DEPTH:
        return gates.Fail("decompose", "decompose_depth", terminal=True,
                          detail=f"already a decomposed child (depth >= "
                                 f"{decomposer.MAX_DEPTH}) — recursion guard",
                          fingerprint_inputs=("archetype", "detail"), parkable=True)
    children = _decompose_children(tid)
    if children is None:
        return gates.Fail("decompose", "children_unreadable",
                          detail="queue.db unreadable — cannot prove the parent is "
                                 "undecomposed, refusing to spawn (wq#1141)")
    if children:
        if all(status == "done" for _, status in children):
            return close_decomposed_parent(ctx, tid, [c for c, _ in children])
        return gates.Fail("decompose", "decompose_has_children",
                          detail="already decomposed into "
                                 + ",".join(f"wq#{c}" for c, _ in children[:8])
                                 + " — never re-decomposed (wq#1141)")
    cap = decomposer_cfg(ctx.cfg).get("max_per_pass", 1)
    if ctx.decomposer_spawned >= cap:
        return gates.Fail("decompose", "decomposer_cap",
                          detail=f"per-pass decomposer cap reached "
                                 f"({ctx.decomposer_spawned}/{cap})")
    unit = f"{router.DECOMPOSER_UNIT_PREFIX}{tid}"
    try:
        running = concurrency.list_running_units(router.DECOMPOSER_UNIT_PREFIX)
    except concurrency.LumenRunError as e:
        return gates.Fail("decompose", "decomposer_units_unreadable", detail=str(e)[:300])
    if unit in running:
        return gates.Fail("decompose", "decomposer_running", detail=f"{unit} is live")
    if ctx.dry_run:
        ctx.decomposer_spawned += 1  # count so dry-run reports the cap honestly
        return gates.Fail("decompose", "would_spawn_decomposer", detail=unit)
    try:
        p = subprocess.run(router.decomposer_cmd(tid), capture_output=True, text=True,
                           timeout=30)
        err = None if p.returncode == 0 else (p.stderr or p.stdout).strip()[:300]
    except (OSError, subprocess.TimeoutExpired) as e:
        err = str(e)[:300]
    if err is not None:
        ntfy(f"dispatcher: decomposer spawn failed for wq#{tid} ({lane}) — see dispatcher.log")
        return gates.Fail("decompose", "decomposer_spawn_failed", detail=err)
    ctx.decomposer_spawned += 1
    return gates.Fail("decompose", "decomposer_spawned", detail=unit)


def close_decomposed_parent(ctx, tid, children):
    """wq#2517 phase 3: every child is done, so the parent epic is delivered — close it
    in the pass instead of spawning a decomposer to rediscover the same fact.
    dispatch_decomposer refuses to spawn while children exist (wq#1141), so without this
    a finished parent would clear its Deps gate and then sit on decompose_has_children
    forever. The done-watch is the wq#992 refill kick: the last child's fworker kicks a
    dispatch pass on exit and the 20-min timer backstops it, so no new watcher runs.

    `--no-artifact` answers wq#759's evidence gate on the record: a decomposed parent's
    evidence lives on its children, each already held to its own done gate. A refused
    close (wq#1673 deliverable gate, ownership fence) pages and leaves the parent open
    for a human — fail-open, never a crashed pass."""
    listing = ", ".join(f"wq#{c}" for c in children)
    if ctx.dry_run:
        return gates.Fail("decompose", "would_close_decomposed_parent", detail=listing)
    try:
        p = subprocess.run(
            [WQ_BIN, "done", str(tid),
             "--notes", f"auto-closed (wq#2517): all {len(children)} sub-tickets done: "
                        f"{listing}",
             "--no-artifact", "decomposed parent; the evidence lives on the done children"],
            capture_output=True, text=True, timeout=60)
        err = None if p.returncode == 0 else (p.stderr or p.stdout).strip()[:300]
    except (OSError, subprocess.TimeoutExpired) as e:
        err = str(e)[:300]
    if err is not None:
        ntfy(f"dispatcher: auto-close of decomposed parent wq#{tid} refused — all "
             f"children done ({listing}) but `wq done` failed; close it by hand",
             priority="high", title="dispatcher decompose close failed")
        return gates.Fail("decompose", "decompose_close_failed", detail=err)
    return gates.Fail("decompose", "decomposed_parent_closed", detail=listing)


def governor_pressure(model):
    return router.governor_pressure(model, state_path=GOVERNOR_STATE,
                                    governor_cli=GOVERNOR_CLI)


def governor_limits():
    """Raw `limits` block from the governor's state.json (usage % + resets_at per pool),
    read directly since pacing needs `resets_at` and router.governor_pressure doesn't
    expose it. Same fail-quiet read as router's own state load: an unreadable/malformed
    file just means pacing has no pools to project (governor_pressure's own fail-closed
    'deny' already covers the case where state is unreadable — MUST 5 layer 1)."""
    try:
        with open(GOVERNOR_STATE) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {}
    limits = st.get("limits") if isinstance(st, dict) else None
    return limits if isinstance(limits, dict) else {}


def lint_capability(tid, t, lane, repo, log):
    """Stage 3.7 DERIVATION half (wq#1437, BROKER_WIRING.md §2.1/§5 slice B): derive
    this candidate's capability needs and log one `capability_lint` event carrying the
    three buckets. Decides nothing on its own — `enforce_capability` below is what
    turns `refused`/`unresolved` into pre-skips and `tags` into a needs write, and
    only in an enforcing mode.

    A candidate whose derivation is EMPTY gets no event: the decision log's one-line-
    per-ticket property (test_every_decision_logged) is the thing the health classifier
    reads, and "this ticket cites nothing" is already counted in pass_end's
    `lint_scanned`/`lint_flagged`.

    Rollback: FOREMAN_NEEDS_LINT_MODE=off (no derivation, no event). Any exception in
    the lint is logged and swallowed — a derivation must never cost a ticket its
    decision (wq#1408 §3.1, same reasoning as the quarantine boundary).
    """
    if not capability_lint.enabled():
        return None
    try:
        derived = capability_lint.derive(
            t, repo=repo, add_dirs=capability_lint.base_add_dirs(t.get("archetype")))
    except Exception as e:  # noqa: BLE001 — see docstring
        log.emit("capability_lint_error", ticket=tid, lane=lane, reason=repr(e)[:300])
        return None
    if derived:
        log.emit("capability_lint", ticket=tid, lane=lane,
                 mode=capability_lint.mode(), applied=False,
                 needs_current=t.get("needs") or "", **derived.as_event())
    return derived


def handle_capability_skip(state, tid, lane, reason, detail, dry_run, log):
    """One of the four §2.2 pre-skips, plus Frank's once-per-ticket-per-24h ping
    naming the exact tag that is missing. Same ledger shape and same dedup window as
    handle_harness_defect_hold — this is that hold moved BEFORE the spawn, which is the
    whole point of the slice: pre-PR#287 wq#1361 spawned 65x and #1401 33x on the same
    unreachable path, and even after the hold landed each still cost one uncharged
    LAUNCH_FAILURE before anything noticed.

    Deduped per (reason, ticket): a ticket that gets its dir tag applied and then trips
    a DIFFERENT capability wall is a new fact and pages again. Returns the skip reason
    (truthy), which is the caller's `continue`."""
    log.skip(tid, lane, "capability", reason, detail=detail)
    now = _utcnow()
    key = f"{reason}:{tid}"
    last = state["capability_ntfy"].get(key)
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=FIRE_NTFY_DEDUP_H):
                return reason  # already pinged Frank within 24h — never flood
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason=reason)
        return reason
    ntfy(f"wq#{tid} ({lane}) not dispatchable: {reason} — {detail}. No worker was "
         f"spawned. Rewrite the ticket (or add the tag by hand) and it dispatches on "
         f"the next pass.", priority="high",
         title="foreman dispatcher: capability pre-skip")
    state["capability_ntfy"][key] = now.isoformat(timespec="seconds")
    save_state(state)
    return reason


def file_grant_proposals(state, tid, unresolved, dry_run, log):
    """wq#1697 fix 2: a read root outside allow.read_dirs (needs_unresolved skip, or the
    advisory dispatch that replaced it) files ONE aq row per root (keyed on the path, not
    the ticket) instead of being noticed silently every pass. The
    24h state dedup keeps aq_add off the per-pass hot path; aq's (source,key) dedup
    bumps the open row across tickets that need the same root."""
    for path in capability_proposal.unresolved_dir_paths(unresolved):
        key = capability_proposal.grant_key(path)
        proposals = state.setdefault("grant_proposals", {})
        if key in proposals:
            continue
        if dry_run:
            log.emit("would_grant_proposal", ticket=tid, path=path, key=key)
            continue
        filed = capability_proposal.file_dir_grant(path, tid)
        log.emit("grant_proposal", ticket=tid, path=path, key=key,
                 reason="filed" if filed else "aq_add_failed")
        if filed:
            proposals[key] = _utcnow().isoformat(timespec="seconds")
            save_state(state)


def enforce_capability(state, tid, t, lane, repo, derived, dry_run, log):
    """Stage 3.7 ENFORCEMENT half — the churn killer (wq#1438, BROKER_WIRING.md §2.2).

    Returns the skip reason (truthy) when the candidate must be skipped, else None. The
    order is the doc's, and the order matters: `refused` and `unresolved` are facts about the ticket TEXT that no
    grant can fix, so they are answered before preflight is asked anything, and
    `preflight` is asked over `ticket.needs ∪ lint.tags` so a path this very pass is
    about to grant does not read as unreachable. Auto-tagging happens last, only for a
    candidate that has already cleared the gate: a ticket that would be skipped anyway
    must not have its `needs` column edited on the way out.

    Fail-open on an internal exception (logged as capability_lint_error, ticket keeps
    its decision): fworker calls the same `capability.preflight` at spawn (§1
    belt-and-braces), so a crash here costs the slot burn this slice exists to save,
    never a wrongly-granted capability. Rollback: FOREMAN_NEEDS_LINT_MODE=warn.

    wq#2131: a ticket whose derivation carries only `advisory` findings is dispatched
    (preflight, the real-reach check, still runs and still decides); it gets one
    `needs_advisory` note and no ntfy. Rollback: FOREMAN_CAPABILITY_ADVISORY=off."""
    if not capability_lint.enforcing():
        return None
    try:
        if derived is not None and derived.refused:
            return handle_capability_skip(
                state, tid, lane, capability.REASON_CAPABILITY_DENIED_BY_POLICY,
                "; ".join(derived.refused), dry_run, log)
        if derived is not None:
            # wq#1697: an outside-read_dirs root is advisory (wq#2131) or unresolved
            # (FOREMAN_CAPABILITY_ADVISORY=off); either way the grant is what fixes it.
            file_grant_proposals(state, tid, [*derived.unresolved, *derived.advisory],
                                 dry_run, log)
        if derived is not None and derived.unresolved:
            return handle_capability_skip(
                state, tid, lane, capability.REASON_NEEDS_UNRESOLVED,
                "; ".join(derived.unresolved), dry_run, log)
        needs = capability.effective_needs(t, derived)
        add_dirs = capability.effective_add_dirs(t.get("archetype"), needs)
        verdict = capability.preflight(t, repo, add_dirs, needs,
                                       advisory_paths=(derived.advisory_paths
                                                       if derived is not None else ()))
        if not verdict.ok:
            return handle_capability_skip(state, tid, lane, verdict.reason,
                                          verdict.summary, dry_run, log)
        tags = capability_lint.appliable_tags(derived)
        if tags:
            written = capability.apply_tags(tid, tags, current_needs=t.get("needs"),
                                            dry_run=dry_run)
            if written:
                log.emit("capability_applied", ticket=tid, lane=lane,
                         mode=capability_lint.mode(), dry_run=dry_run,
                         tags=[f"{n}({a})" for n, a in tags], needs=written)
        if derived is not None and derived.advisory:
            # wq#2131: advisory-only findings never hold a ticket and never page. One
            # note per distinct finding set, recognised from the ticket's own notes.
            fp, note = capability_lint.advisory_note(derived)
            if not capability_lint.advisory_noted(t.get("notes"), fp):
                if not dry_run:
                    capability._wq("note", str(tid), note)
                log.emit("capability_advisory_noted", ticket=tid, lane=lane,
                         dry_run=dry_run, fingerprint=fp, advisory=list(derived.advisory))
    except Exception as e:  # noqa: BLE001 — see docstring
        log.emit("capability_lint_error", ticket=tid, lane=lane, stage="enforce",
                 reason=repr(e)[:300])
    return None


PACING_HOOK = pacing.decide  # seam: tests replace to prove the dispatcher <-> pacing wiring


def handle_pacing_throttle(state, verdict, pacing_cfg, dry_run, log):
    """Frank-visible ntfy on throttle (wq#716 acceptance), deduped per-pool so a
    multi-ticket pass doesn't page him once per skipped ticket."""
    now = _utcnow()
    dedup_h = pacing_cfg.get("ntfy_dedup_hours", pacing.DEFAULT_CONFIG["ntfy_dedup_hours"])
    last = state["pacing_ntfy"].get(verdict.pool)
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=dedup_h):
                return
        except ValueError:
            pass
    msg = f"dispatcher pacing: {verdict.action} on {verdict.pool} — {verdict.reason}"
    if verdict.detail:
        msg += f" ({verdict.detail})"
    if dry_run:
        log.emit("would_ntfy", reason="pacing_throttle", pool=verdict.pool,
                 action=verdict.action)
        return
    ntfy(msg, priority="default", title="dispatcher pacing throttle")
    state["pacing_ntfy"][verdict.pool] = now.isoformat(timespec="seconds")
    save_state(state)


# ---- candidate scan (§3 stage 2: read-only, effective_pri order) ---------------------

def _scan_lane(lane, log):
    """One lane's open tickets as scan rows; [] after logging a `wq list` failure."""
    try:
        p = subprocess.run([WQ_BIN, "list", lane, "--status", "open",
                            "--sort", "effective"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.emit("error", stage="scan", lane=lane, reason=f"wq list failed: {e}")
        return []
    if p.returncode != 0:
        log.emit("error", stage="scan", lane=lane,
                 reason=f"wq list rc={p.returncode}: {p.stderr.strip()}")
        return []
    rows = []
    for line in p.stdout.splitlines():
        m = LIST_LINE_RE.match(line)
        if not m:
            continue
        tid, tlane, pri, ep, status = m.groups()
        if status != "open" or tlane != lane:
            continue
        rows.append({"id": int(tid), "lane": lane, "pri": int(pri),
                     "epri": int(ep) if ep is not None else int(pri)})
    return rows


def scan_candidates(cfg, log):
    """Open tickets of every enabled lane, `(epri, id)` sorted. Human-owned lanes are never
    candidates (step 8): a `frank_gated` line per Frank's to-do per pass was 13 of the 120
    candidates and the F7 counting hazard — see sweep_human_lanes for what they still get."""
    out = []
    for lane, policy in sorted(cfg["lanes"].items()):
        if not policy.get("enabled"):
            continue  # allowlist: disabled lanes are never scanned (§4)
        if lane in HUMAN_LANES and not hygiene_legacy():
            continue
        out.extend(_scan_lane(lane, log))
    out.sort(key=lambda c: (c["epri"], c["id"]))  # wq#661 aging respected across lanes
    return out


def sweep_human_lanes(cfg, log):
    """wq#1478 LIVENESS_GATE §12.4 for the lanes scan_candidates no longer emits: a Frank-gate
    whose premise is already met on live state is closed before Frank sees it. That hook ran
    as the human-lane gate's first act, so dropping the candidates would have dropped it too.
    Can only close, never dispatch; a non-closing verdict is silent."""
    for lane, policy in sorted(cfg["lanes"].items()):
        if policy.get("enabled") and lane in HUMAN_LANES:
            for row in _scan_lane(lane, log):
                _liveness_autoclosed(row["id"], None, log)


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


# ---- wq#1261 (#13 item 1): no probe, no dispatch ---------------------------------------
# "A fire ticket without a probe is refused dispatch by the router entirely — a fire you
# can't re-verify is a FRANK page, not a worker." (ISSUE-SOLUTIONS-fable.md §4 item 1)

def handle_no_probe_fire(state, tid, lane, dry_run, log):
    log.skip(tid, lane, "fire_probe", "fire_no_probe",
             detail="no fire_probe: {cmd, clear_when_exit} line and no legacy aq "
                    "Probe:/aq row: reference in ticket detail")
    now = _utcnow()
    last = state["fire_ntfy"].get(f"noprobe-{tid}")
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=FIRE_NTFY_DEDUP_H):
                return  # already pinged Frank within 24h — never flood
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason="fire_no_probe")
        return
    ntfy(f"wq#{tid} ({lane}) is dispatch-ready but has no re-verify probe — refusing to "
         f"dispatch (a fire without a probe can't auto-close on recovery). Add "
         f'`fire_probe: {{"cmd": "...", "clear_when_exit": 0}}` to the ticket detail: '
         f'`wq edit {tid} --detail "..."`.', priority="high",
         title="fire ticket has no probe")
    state["fire_ntfy"][f"noprobe-{tid}"] = now.isoformat(timespec="seconds")
    save_state(state)


# ---- runaway tickets (wq#718 AUTO-6, DESIGN addendum §13) ----------------------------

def handle_runaway_stuck(state, tid, lane, attempts, dry_run, log):
    log.skip(tid, lane, "runaway", "runaway_stuck",
             detail=f"{attempts} attempts, still not done")
    now = _utcnow()
    last = state["runaway_ntfy"].get(str(tid))
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=FIRE_NTFY_DEDUP_H):
                return  # already pinged Frank within 24h — never flood
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason="runaway_stuck")
        return
    ntfy(f"wq#{tid} ({lane}) looks stuck: {attempts} attempts without reaching done. "
         f"Dispatcher will keep skipping it every pass until it's fixed or manually "
         f"intervened.", priority="high", title="foreman dispatcher: runaway ticket")
    state["runaway_ntfy"][str(tid)] = now.isoformat(timespec="seconds")
    save_state(state)


def handle_harness_defect_hold(state, tid, lane, summary, dry_run, log):
    """wq#1415: hold a ticket whose last recorded failure is HARNESS_DEFECT (see
    runaway.harness_defect_ticket_ids()) so it stops being re-claimed on every
    event-driven refill pass (fworker.py trigger_refill) while the underlying harness
    defect is still unfixed. Never auto-blocks the ticket — it self-clears the moment a
    later attempt gets past preflight — so this only needs the same dedup-ntfy pattern
    as handle_runaway_stuck, not a new escalation path."""
    log.skip(tid, lane, "harness_defect", "harness_defect_hold", detail=summary)
    now = _utcnow()
    last = state["harness_defect_ntfy"].get(str(tid))
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=FIRE_NTFY_DEDUP_H):
                return  # already pinged Frank within 24h — never flood
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason="harness_defect_hold")
        return
    ntfy(f"wq#{tid} ({lane}) held: last failure was HARNESS_DEFECT ({summary}). "
         f"Dispatcher will keep skipping it every pass until the defect is fixed and a "
         f"later attempt gets past preflight.", priority="high",
         title="foreman dispatcher: harness-defect hold")
    state["harness_defect_ntfy"][str(tid)] = now.isoformat(timespec="seconds")
    save_state(state)


def handle_spawn_loop(state, tid, lane, info, dry_run, log, t=None):
    """wq#1703: stop re-spawning a ticket that has burned SPAWN_LOOP_MAX spawns inside
    the guard's window without one attempt finishing, and open an aq row so the loop is
    visible. Both breakers above key on an artifact the worker writes (attempt-N.json,
    failure.json); this one keys on the spawn itself, which is the only thing still
    observable when the worker dies before writing either (ISSUE-LOG #283: wq#1543
    spawned 31x in 3h that way). The first trip only skips — the burst ages out of the
    window, or a finished attempt lifts the guard and hands it to the wq#718 breaker.

    Step 8: the roll-off used to repeat forever. Each loop episode is recorded once
    (info["new_trip"]); the SECOND inside 24 h blocks the ticket with
    `SPAWN_LOOP: <last release note from t>` and always pages, since a block is a state
    change the 24 h ntfy dedup must not swallow. FOREMAN_CHARGE_MODE=legacy: skip only.

    wq#2581: a ticket whose CURRENT model is a local rung gets one more option before
    that second-trip block -- router.escalate_off_local hands it to the next non-local
    rung in its own archetype chain instead, on the same reasoning fworker.py's own
    exhaustion path uses (a local ticket that fails never otherwise reaches a cloud
    model). Idempotent per ticket (notes marker) and itself decides when there is
    nothing to escalate to, in which case this falls through to the ordinary block."""
    detail = (f"{info['spawns']} spawns since {info['since']}, "
              f"{info['attempts']} finished attempts")
    log.skip(tid, lane, "spawn_loop", "spawn_loop", detail=detail)
    now = _utcnow()
    blocked = None
    escalated_to = None
    if not self_heal.charge_mode_legacy():
        if info.get("new_trip") and not dry_run:
            spawn_loop_guard.record_trip(state, tid, now)
            save_state(state)
        if info.get("trips_24h", 0) >= spawn_loop_guard.BLOCK_ON_TRIP:
            reason = spawn_loop_guard.block_reason((t or {}).get("notes"))
            model = (t or {}).get("model")
            if dry_run:
                verb = "would_escalate" if router.is_local_rung(model) else "would_block"
                log.emit(verb, ticket=tid, lane=lane, reason=reason)
            else:
                if router.is_local_rung(model):
                    esc_reason = (f"SPAWN_LOOP ({info['spawns']} spawns since "
                                  f"{info['since']}, no finished attempt)")
                    ok, esc_detail = router.escalate_off_local(tid, reason=esc_reason)
                    if ok:
                        escalated_to = esc_detail
                        log.emit("spawn_loop_escalated", ticket=tid, lane=lane,
                                 to=escalated_to, trips_24h=info["trips_24h"])
                if escalated_to:
                    pass
                elif spawn_loop_guard.block_ticket(tid, reason, WQ_BIN):
                    blocked = reason
                    log.emit("spawn_loop_blocked", ticket=tid, lane=lane, reason=reason,
                             trips_24h=info["trips_24h"])
                else:
                    log.emit("error", stage="spawn_loop", ticket=tid, lane=lane,
                             reason="block_failed", detail=reason)
    last = state["spawn_loop_ntfy"].get(str(tid))
    if last and not (blocked or escalated_to):
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=FIRE_NTFY_DEDUP_H):
                return  # already filed + pinged within 24h — never flood
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason="spawn_loop")
        return
    if escalated_to:
        # wq#2581: the dispatcher WILL keep dispatching this ticket (on its new rung) —
        # no aq row, since spawn_loop_guard.aq_message()'s fixed text ("stopped
        # re-spawning it... skips every pass") would be false here. Still worth a
        # low-priority note (a self-healing action, not a human-needed one), and still
        # bypasses the ntfy dedup above like a block does — a rung change is a state
        # change too.
        ntfy(f"wq#{tid} ({lane}) spawn-loop guard tripped twice in 24h on a local rung: "
             f"{detail}. Escalated to {escalated_to} instead of blocking.",
             title="foreman dispatcher: spawn loop escalated")
        state["spawn_loop_ntfy"][str(tid)] = now.isoformat(timespec="seconds")
        save_state(state)
        return
    filed = spawn_loop_guard.aq_row(tid, info)
    log.emit("aq_row", ticket=tid, lane=lane, stage="spawn_loop",
             reason="filed" if filed else "aq_add_failed", detail=detail)
    if blocked:
        ntfy(f"wq#{tid} ({lane}) tripped the spawn-loop guard twice in 24h: {detail}. "
             f"Blocked it ({blocked}) — it needs a human look, not another respawn.",
             priority="high", title="foreman dispatcher: spawn loop blocked")
    else:
        ntfy(f"wq#{tid} ({lane}) is in a spawn loop: {detail}. Dispatcher has stopped "
             f"re-spawning it and opened an aq row — look for a worker dying before it "
             f"writes attempt-N.json.", priority="high",
             title="foreman dispatcher: spawn loop")
    state["spawn_loop_ntfy"][str(tid)] = now.isoformat(timespec="seconds")
    save_state(state)


PERSISTENT_PREFLIGHT_REASONS = ("static lint failed", "live proof found permission denials")


def _preflight_failure_is_persistent(result):
    return result.reason in PERSISTENT_PREFLIGHT_REASONS


def _reprove_once(repo):
    """One harness_preflight.preflight() call, never raising: a raw exception used to
    abort the whole pass (wq#1384) — it is a result like any other."""
    try:
        return harness_preflight.preflight(repo)
    except Exception as e:  # noqa: BLE001 — see docstring
        return harness_preflight.PreflightResult(
            repo, ok=False, reason=f"preflight raised: {e!r}", static_failures=[], denials=[])


def handle_stale_preflight(state, tid, lane, repo, reason, dry_run, log, defer=None,
                           post_lock=False):
    """wq#1372: dispatch_gate_check() failing used to just mean `continue` forever —
    a repo's worker-settings.json or preflight_cmds changing made its stamp stale and
    the lane sat idle, silently, until a human noticed and re-ran harness-preflight by
    hand (twice in one day per the ticket). Re-prove right here instead: a clean proof
    writes a fresh stamp and the caller falls through to spawn this same pass.

    wq#1409 §2.2: only a HARD-stale stamp reaches here now (the allow/deny/defaultMode/
    cmds set really changed — harness_preflight.stamp_status); benign churn is
    soft-stale and never comes this way. Two failure classes:
      persistent  static lint failed / live denials — the profile is broken; page once
                  (high), 1h cooldown, skip. Retrying the same proof cannot help.
      transient   the proof ERRORED (FileNotFoundError, TimeoutExpired, claude exit,
                  raised) — retry in-pass on REPROVE_RETRY_BACKOFF_S, then page urgent
                  with the literal exception + cooldown. On 2026-09-03 every one of
                  these was the wq#1407 PATH bug and each one silently cost an hour.
    preflight_ntfy[repo] is {ts, attempts, last_error, paged}; a clean proof after a
    page sends a default-priority "Recovered:" ntfy so the aq row can auto-resolve.
    Returns True when the repo now has a fresh stamp.

    Step 8 lock hygiene: this ladder can run 420 s and used to run INSIDE the pass's slock, so
    every other pass read lock_busy for as long. With `defer` (a list; _spawn passes the pass's
    own) a stale stamp is instead recorded once per repo, the ticket skips as no_fresh_stamp,
    and run_pass calls this again with `post_lock=True` after slock_release, same process. That
    call logs `info` events, not a second `skip` for a ticket already decided. A reprove
    already running elsewhere (per-repo slock) is left to that run. --dry-run, `defer=None`
    and FOREMAN_DISPATCH_HYGIENE=legacy keep the in-lock behaviour."""
    name = harness_preflight.permission_profiles.repo_name(repo)
    now = _utcnow()

    def skip(why, detail):
        if post_lock:
            log.emit("info", stage="preflight", reason=why, ticket=tid, lane=lane, detail=detail)
        else:
            log.skip(tid, lane, "preflight", why, detail=detail)

    last = state["preflight_ntfy"].get(name)
    if last:
        try:
            if now - _ledger_ts(last) < timedelta(hours=PREFLIGHT_REPROVE_COOLDOWN_H):
                skip("no_fresh_stamp", reason)
                return False
        except (TypeError, ValueError, AttributeError):
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason="preflight_reprove", repo=name)
        skip("no_fresh_stamp", reason)
        return False
    if defer is not None and not post_lock and not hygiene_legacy():
        if all(d[2] != repo for d in defer):
            defer.append((tid, lane, repo, reason))
        skip("no_fresh_stamp", f"{reason} (reprove runs after the pass lock is released)")
        return False
    reprove_lock = None
    if post_lock:
        reprove_lock = f"foreman-reprove-{name}"
        holder = f"foreman-dispatcher:{os.getpid()}"
        p = subprocess.run([SLOCK_BIN, "acquire", reprove_lock, "--holder", holder,
                            "--ttl", str(REPROVE_LADDER_BUDGET_S // 60 + 3)],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            log.emit("info", stage="preflight", reason="reprove_in_flight", ticket=tid,
                     lane=lane, detail=f"{name}: another pass holds {reprove_lock}")
            return False
    try:
        return _reprove_stale(state, tid, lane, repo, name, reason, now, last, log, skip,
                              post_lock)
    finally:
        if reprove_lock:
            subprocess.run([SLOCK_BIN, "release", reprove_lock, "--holder",
                            f"foreman-dispatcher:{os.getpid()}"],
                           capture_output=True, text=True, timeout=30)


def _commit_preflight_ntfy(state, name, entry, post_lock):
    """Set (or, entry=None, clear) state["preflight_ntfy"][name] and save. After the lock is
    released other passes write state.json while a reprove runs for minutes, so a post-lock
    commit re-reads it first rather than overwriting their lane counts with this pass's copy."""
    target = load_state() if post_lock else state
    if entry is None:
        target["preflight_ntfy"].pop(name, None)
    else:
        target["preflight_ntfy"][name] = entry
    save_state(target)


def _reprove_stale(state, tid, lane, repo, name, reason, now, last, log, skip, post_lock=False):
    """The reprove ladder of handle_stale_preflight (wq#1409 §2.2); `skip` is its decision line."""
    was_paged = isinstance(last, dict) and bool(last.get("paged"))
    started = time.monotonic()
    attempts = 0
    result = None
    for backoff in REPROVE_RETRY_BACKOFF_S:
        if backoff:
            if time.monotonic() - started + backoff > REPROVE_LADDER_BUDGET_S:
                log.emit("info", stage="preflight", reason="reprove_ladder_budget",
                         detail=f"{name}: {attempts} attempt(s), no time left for a "
                                f"{backoff}s retry under {REPROVE_LADDER_BUDGET_S}s")
                break
            log.emit("info", stage="preflight", reason="reprove_retry",
                     detail=f"{name}: attempt {attempts} errored ({result.reason}); "
                            f"retrying in {backoff}s")
            _sleep(backoff)
        attempts += 1
        result = _reprove_once(repo)
        if result.ok or _preflight_failure_is_persistent(result):
            break

    if result.ok:
        log.emit("info", stage="preflight", reason="auto_reproved",
                 detail=f"{name}: stale stamp ({reason}) reproved clean on attempt {attempts}")
        _commit_preflight_ntfy(state, name, None, post_lock)
        if was_paged:
            ntfy(f"Recovered: preflight reprove for {name} succeeded (attempt {attempts}); "
                 f"wq#{tid} ({lane}) dispatching.",
                 priority="default", title="foreman dispatcher: preflight recovered")
        return True

    problems = "; ".join(result.static_failures + result.denials) or result.reason
    if _preflight_failure_is_persistent(result):
        ntfy(f"preflight auto-reprove failed for {name}: {problems}. wq#{tid} ({lane}) and "
             f"every other {name} ticket will keep idling until this is fixed.",
             priority="high", title="foreman dispatcher: preflight reprove failed")
    else:
        ntfy(f"preflight reprove for {name} ERRORED {attempts}x in one pass "
             f"(retry ladder {REPROVE_RETRY_BACKOFF_S}): {problems}. wq#{tid} ({lane}) and "
             f"every other {name} ticket idle until the next pass after "
             f"{PREFLIGHT_REPROVE_COOLDOWN_H}h.",
             priority="urgent", title="foreman dispatcher: preflight reprove erroring")
    _commit_preflight_ntfy(state, name, {"ts": now.isoformat(timespec="seconds"),
                                         "attempts": attempts, "last_error": problems[:300],
                                         "paged": True}, post_lock)
    skip("reprove_failed", f"{problems} (attempts={attempts})")
    return False


def repo_gate_check(cache, repo):
    """wq#1408 §1.5/§3.1: dispatch_gate_check() itself can raise (malformed
    repos.d/<repo>.json, base.json, or any profile fragment — permission_profiles.py's
    own _load_json is a bare `json.load`; an unwritable/unreadable file surfaces as
    OSError). Unwrapped, that one JSON typo killed every ticket of that repo, every
    pass, one at a time. Cache the (ok, reason, errored) result per repo per pass —
    same digest, same answer for every candidate of that repo this pass — so a broken
    profile costs one exception and one skip reason (`profile_unreadable`) instead of
    one exception per ticket. Not a real dispatch_gate_check result: `errored=True`
    means the gate itself is broken, not that the stamp is merely stale."""
    if repo not in cache:
        try:
            ok, reason = harness_preflight.dispatch_gate_check(repo)
            cache[repo] = (ok, reason, False)
        except Exception as e:  # noqa: BLE001 — see docstring
            cache[repo] = (False, f"profile_unreadable: {e!r}", True)
    return cache[repo]


def handle_soft_stale_preflight(cache, tid, lane, repo, reason, dry_run, log):
    """wq#1409 §3.2: dispatch_gate_check() said (True, "stamp_soft_stale ..."). Emit
    the info event once per repo per pass (the gate cache is per pass, so mark it
    there) and reseal the stamp's settings_hash — never a reprove, never a skip."""
    name = harness_preflight.permission_profiles.repo_name(repo)
    key = ("soft_stale_logged", repo)
    if key in cache:
        return
    cache[key] = True
    log.emit("info", stage="preflight", reason="stamp_soft_stale", ticket=tid, lane=lane,
             detail=reason)
    if dry_run:
        return
    try:
        harness_preflight.reseal_stamp(repo)
    except Exception as e:  # noqa: BLE001 — resealing is hygiene, never load-bearing
        log.emit("error", stage="preflight", reason="reseal_failed",
                 detail=f"{name}: {e!r}")


def handle_profile_unreadable(state, tid, lane, repo, reason, dry_run, log):
    """wq#1408: the gate itself is broken (see repo_gate_check) — this is not a stale
    stamp handle_stale_preflight can reprove its way out of, it's a JSON syntax error
    or permission problem a human has to fix. Skip loudly, once per repo per pass
    (repo_gate_check's cache already guarantees that), and alert once per repo per
    1h (same dedup window as the reprove-failure alert) so a persistently broken
    profile can't page every pass forever."""
    name = harness_preflight.permission_profiles.repo_name(repo)
    log.skip(tid, lane, "preflight", "profile_unreadable", detail=reason)
    now = _utcnow()
    last = state["gate_error_ntfy"].get(name)
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=PREFLIGHT_REPROVE_COOLDOWN_H):
                return
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, lane=lane, reason="profile_unreadable", repo=name)
        return
    ntfy(f"dispatcher: {name}'s preflight profile is unreadable: {reason}. wq#{tid} "
         f"({lane}) and every other {name} ticket will keep idling until this is fixed.",
         priority="high", title="foreman dispatcher: profile unreadable")
    state["gate_error_ntfy"][name] = now.isoformat(timespec="seconds")
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

# wq#2088 (research/DISPATCH_STALL_RCA.md §4.3): one pass, two partitions. `_gate_candidate`
# runs every pool-independent stage once per candidate and resolves the model; the ready
# tickets are then split by pool and admitted by `_admit_local` (first, no cloud gate of any
# kind) and `_admit_cloud`. A stop in one partition never reaches the other. An admission
# returns True (spawn), False (skipped, logged) or a str (stop reason: end this partition).
PASS_DEADLINE_S = 240  # soft: the cloud partition ends with halt stage=deadline; local ran first


class _PassCtx:
    """Per-pass working state shared by the gate and both partitions."""

    def __init__(self, cfg, log, state, dry_run):
        self.cfg, self.log, self.state, self.dry_run = cfg, log, state, dry_run
        self.deadline = time.monotonic() + cfg.get("pass_deadline_s", PASS_DEADLINE_S)
        self.dep_cache, self.pressure_cache, self.gate_cache = {}, {}, {}
        self.stuck, self.harness_defects = {}, {}
        self.units = None            # live fworker units; None = re-read before next use
        self.unit_is_local = {}      # unit -> bool, one `wq show` per unit per pass
        self.unit_model = {}         # unit -> model, same lifetime (local units only)
        self._capacity = None        # wq#2130: LocalCapacity, probes once per pass
        self.psi_tight = False       # box pressure: throttles both partitions
        self.cloud_tight = False     # no-resets_at governor fallback: cloud only
        self.limits, self.pacing_cfg = {}, {}
        self.burn_cfg, self.burn_obs = {}, None
        self.burn_cache, self.burn_logged = {}, set()  # pools tuple -> (allowed | None, detail)
        self.ceiling_cache, self.ceiling_logged = {}, set()  # wq#2426: pools tuple -> (ceiling, detail)
        self.spawned = {"local": 0, "cloud": 0}
        self.decomposer_spawned = 0  # wq#2516: decomposer units this pass, capped separately
        self.lane_capped = {"local": 0, "cloud": 0}
        self.local_slots_full = 0
        self.local_paused = False    # wq#2130: circuit_breaker's local pause is active
        self.lint_scanned = self.lint_flagged = 0  # wq#1437 stage 3.7 (warn): measurement only
        self.quarantined = []
        self.deferred_reproves = []  # step 8: (tid, lane, repo, reason) reproved after slock release
        self.gate_salt = ""          # step 11: config BLOB sha, mixed into every park fingerprint

    def refresh_units(self):
        self.units = concurrency.list_running_units()

    def unit_counts(self):
        """(local, cloud) running units. Local-ness is cached per unit for the pass."""
        local = cloud = 0
        for u in self.units:
            if u not in self.unit_is_local:
                self.unit_is_local[u] = concurrency.is_local_unit(u)
            if self.unit_is_local[u]:
                local += 1
            else:
                cloud += 1
        return local, cloud

    def local_unit_models(self):
        """Model of every running local unit (wq#2130) — what LocalCapacity subtracts
        from a backend's slots. Same per-pass `wq show` cache discipline as unit_counts.
        wq#2154: the unit's own --model wins over the ticket column, which keeps the group
        name for a ticket the dispatcher resolved to a member."""
        models = []
        for u in self.units or []:
            if u not in self.unit_is_local:
                self.unit_is_local[u] = concurrency.is_local_unit(u)
            if not self.unit_is_local[u]:
                continue
            if u not in self.unit_model:
                m = concurrency.TICKET_UNIT_RE.match(u)
                self.unit_model[u] = (concurrency.unit_model(u)
                                      or (concurrency.ticket_model(m.group("id")) if m else None))
            models.append(self.unit_model[u])
        return models

    def capacity(self):
        """The pass's LocalCapacity, or None under FOREMAN_LOCAL_CAPACITY=static."""
        if local_capacity.capacity_mode() == "static":
            return None
        if self._capacity is None:
            self._capacity = local_capacity.LocalCapacity(
                self.cfg.get("local_backends"), self.local_unit_models)
        return self._capacity

    def burn_allowed(self, pools):
        if pools not in self.burn_cache:
            self.burn_cache[pools] = burn_rate.allowed_concurrent(
                self.limits, self.cfg["cloud_ceiling"], self.burn_cfg,
                observed=self.burn_obs, pools=pools)
        return self.burn_cache[pools]

    def cloud_ceiling(self, pools):
        """wq#2426: the budget-aware cloud ceiling for `pools` — config cloud_ceiling
        tapered by the spendable budget above Frank's reserve (burn_rate.budget_ceiling).
        Applies on every cloud admission, whatever the governor's pressure verdict."""
        if pools not in self.ceiling_cache:
            self.ceiling_cache[pools] = burn_rate.budget_ceiling(
                self.limits, self.cfg["cloud_ceiling"], self.burn_cfg, pools=pools)
        return self.ceiling_cache[pools]

    def tight(self, part):
        return self.psi_tight or (part == "cloud" and self.cloud_tight)


def _apply_parking(ctx, candidates):
    """Step 11: drop tickets parked in dispatch_gate from the pass and unpark the ones whose
    fingerprint changed or whose recheck_after passed. Returns (to_evaluate, n_parked).

    Each unpark logs one `gate_unparked` line (a parked ticket logged once when parked, in
    gates.run, and logs nothing until this). FAIL-OPEN: FOREMAN_GATE_STORE=off or any error
    evaluates every candidate, exactly as before the store existed. A dry-run reads the store
    and reports the same partition but deletes nothing."""
    if not gate_store.enabled():
        return candidates, 0
    try:
        evaluate, parked, released = gate_store.partition(candidates, ctx.gate_salt)
        for cand, why, row in released:
            if not ctx.dry_run:
                gate_store.unpark(cand["id"])
            ctx.log.emit("gate_unparked", ticket=cand["id"], lane=cand["lane"],
                         reason=row["reason"], why=why, dry_run=ctx.dry_run)
    except Exception as e:  # noqa: BLE001 — parking is an optimisation, never a decision
        ctx.log.emit("error", stage="gate_store", reason="partition_failed",
                     detail=repr(e)[:300])
        return candidates, 0
    return evaluate, len(parked)


def _gate_candidate(ctx, cand):
    """Stages 3-7 + model resolution — nothing here depends on which pool the ticket draws
    from. Runs gates.GATES in DISPATCH_DISSECTION §2.2 order; the first Fail has already
    been logged as one `skip` event. Returns a ready record, or None."""
    g = gates.GateInput(sys.modules[__name__], ctx, cand)
    if gates.run(g) is not gates.PASS:
        return None
    return {"cand": cand, "t": g.t, "repo": g.repo, "policy": g.policy,
            "fire_exempt": g.fire_exempt, "model": g.model,
            "part": "local" if concurrency.is_local_model(g.model) else "cloud"}


def _tight_pri_skip(ctx, r):
    """wq#1699: under tight mode only epri<=min_pri tickets run — the aged effective priority
    the scan sorted on (wq#661), not raw pri: an aged p2 at ep1 sorts first and must pass."""
    cand, min_pri = r["cand"], ctx.cfg["tight_mode"]["min_pri"]
    if cand["epri"] <= min_pri:
        return False
    ctx.log.skip(cand["id"], cand["lane"], "budget", "tight_pri", model=r["model"],
                 detail=f"epri {cand['epri']} (pri {cand['pri']}) > tight min_pri {min_pri}")
    return True


def _admit_local(ctx, r):
    """Local rung (wq#2081/wq#2088): bounded by the boxes, never by the Anthropic login —
    no governor, burn-rate, pacing or cloud_ceiling, and nothing here can stop a cloud
    ticket. A full local lane skips only local tickets. Only box (PSI) pressure applies. Returns True to
    proceed to spawn."""
    tid, lane, model = r["cand"]["id"], r["cand"]["lane"], r["model"]
    if ctx.psi_tight and _tight_pri_skip(ctx, r):
        return False
    local_ceiling = ctx.cfg.get("local_ceiling", concurrency.LOCAL_CEILING)
    n_local, _ = ctx.unit_counts()
    if n_local >= local_ceiling:
        ctx.local_slots_full += 1
        ctx.log.skip(tid, lane, "concurrency", "local_slots_full", model=model,
                     detail=f"{n_local} local unit(s) at ceiling {local_ceiling}")
        return False
    # wq#2130: local_ceiling above is now only the upper clamp. A model with a local_backends
    # row is sized by its own live slots; one without (cheap_cloud, or config not yet carrying
    # the block) and FOREMAN_LOCAL_CAPACITY=static keep the ceiling as the only bound.
    # wq#2154: a group model (local-worker) is a routing name only. It is resolved to a member
    # here — r["member"] then carries the slot accounting, the spawn env and the worker's
    # --model — so the group name never reaches the governor CLI or fworker.
    cap = ctx.capacity()
    if cap is None:
        r["member"] = local_capacity.first_member(ctx.cfg.get("local_backends"), model)
    elif cap.is_group(model):
        member = _routed_member(ctx, r, cap, model) or cap.pick_member(model)
        if member is None:
            ctx.local_slots_full += 1
            ctx.log.skip(tid, lane, "concurrency", "local_slots_full", model=model,
                         detail=f"{model}: no healthy member with a free slot (all full, "
                                f"probe failed or cooling down)")
            return False
        r["member"] = member
    elif cap.knows(model):
        free = cap.free_slots(model)
        if free <= 0:
            ctx.local_slots_full += 1
            ctx.log.skip(tid, lane, "concurrency", "local_slots_full", model=model,
                         detail=f"{model}: 0 free slot(s) (backend full, probe failed or "
                                f"cooling down)")
            return False
    return True


def _prompt_estimate(t):
    """Lower bound on the prompt fworker will build: the ticket's own text plus the local CLI's fixed
    request. The rendered prompt is longer, so this only ever under-filters a small-ctx backend."""
    import charging
    return charging.prompt_tokens_est(f"{t.get('title') or ''}\n{t.get('detail') or ''}", cli_profile=True)


def _routed_member(ctx, r, cap, group):
    """wq#2255 (INFERENCE_NODE_REGISTRY §7.1): the group member the registry ranks best for this ticket's
    archetype — trusted, served_ctx clearing the prompt estimate, highest measured success then prefill_tps —
    that has a free slot. The member is the backend id (what local_capacity accounts slots under); the
    FAMILY.NODE name goes in the log. None falls through to pick_member: the registry is a preference layered
    on the group, never a new way for a local ticket to be refused."""
    try:
        import inference_registry
        import model_swap
        t = r["t"]
        members = set(cap._members(local_capacity.base_name(group)) or [])
        ticket = {"archetype": t.get("archetype"), "prompt_tokens_est": _prompt_estimate(t)}
        pick = model_swap.route(inference_registry.load(), ticket,
                                usable=lambda c: c["backend"] in members and cap.free_slots(c["backend"]) > 0)
        if pick is None:
            return None
        ctx.log.emit("info", stage="model_route", ticket=r["cand"]["id"], model=pick["model"],
                     member=pick["backend"], swap=pick["swap"], skipped=pick["skipped"])
        return pick["backend"]
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        ctx.log.emit("error", stage="model_route", reason="route_failed", detail=repr(e)[:300])
        return None


def _model_swap_step(ctx, ready):
    """wq#2255 (INFERENCE_NODE_REGISTRY §7.3): swap the resident llama-server model on a node when the
    admission conditions hold. Runs after admissions so the in-flight count includes this pass's spawns;
    never load-bearing. FOREMAN_MODEL_SWAP=off logs decisions and moves no unit."""
    try:
        if ctx.capacity() is None:
            return
        import inference_registry
        import model_swap
        reg = inference_registry.load()
        counts = {}

        def in_flight(node):   # asked only for a node with a swappable model, so a registry with none costs no lumen-run call
            if not counts:
                ctx.refresh_units()  # admissions above spawned units
                counts.update(model_swap.in_flight_by_node(reg, ctx.local_unit_models()))
            return counts.get(node, 0)
        pending = [{"archetype": r["t"].get("archetype"), "prompt_tokens_est": _prompt_estimate(r["t"])}
                   for r in ready["local"] if not r.get("spawned")]
        sig = model_swap.Signals(in_flight, pending)
        model_swap.run(sig, dry_run=ctx.dry_run,
                       emit=lambda kind, **kw: ctx.log.emit("info", stage="model_swap", what=kind, **kw))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        ctx.log.emit("error", stage="model_swap", reason="step_failed", detail=repr(e)[:300])


def _node_power_step(ctx, ready):
    """wq#2254 (INFERENCE_NODE_REGISTRY §6): wake a cold node on sustained local starvation, power one
    down when idle. Runs after admissions so the counters see this pass's outcome; never load-bearing —
    a failure is logged and the pass carries on. FOREMAN_NODE_POWER=off logs decisions and moves no AC."""
    try:
        cap = ctx.capacity()
        if cap is None:      # FOREMAN_LOCAL_CAPACITY=static has no health view to judge starvation by
            return
        import node_power    # lazy: pulls harness/inference_registry (PyYAML), which must not gate a pass

        def running():
            ctx.refresh_units()  # admissions above spawned units
            return ctx.local_unit_models()
        sig = node_power.Signals(ctx.local_slots_full, cap.free_slots, running,
                                 max(0, len(ready["local"]) - ctx.spawned["local"]))
        node_power.run(sig, dry_run=ctx.dry_run,
                       emit=lambda kind, **kw: ctx.log.emit("info", stage="node_power", what=kind, **kw))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001
        ctx.log.emit("error", stage="node_power", reason="step_failed", detail=repr(e)[:300])


def _admit_cloud(ctx, r):
    """Stages 8/8b/9 for a cloud model: governor verdict, burn-rate, pacing, cloud_ceiling.
    A stop here is a stop of the cloud partition only. Returns True to proceed to spawn,
    False to skip this ticket, a stop-reason str to end the partition."""
    cfg, log, state = ctx.cfg, ctx.log, ctx.state
    cand, model = r["cand"], r["model"]
    tid, lane = cand["id"], cand["lane"]
    tight_cfg = cfg["tight_mode"]

    # stage 8 — budget (MUST 5): governor verdict + floors via router
    pressure = ctx.pressure_cache.get(model)
    if pressure is None:
        pressure = governor_pressure(model)
        ctx.pressure_cache[model] = pressure
    if pressure == "deny":
        log.skip(tid, lane, "budget", "governor_deny", model=model)
        return False
    burn = None  # (pools, allowed_concurrent) when the controller owns this ticket
    if pressure == "tight":
        allowed, burn_detail = ctx.burn_allowed(burn_rate.pools_for_model(model))
        if allowed is None:
            if not ctx.cloud_tight:  # no percent/resets_at anywhere: old binary clamp
                ctx.cloud_tight = True
                log.emit("tight_mode", model=model,
                         reason=f"pressure tight: max {tight_cfg['max_spawns_per_pass']} "
                                f"spawn(s), epri<={tight_cfg['min_pri']} only",
                         detail=burn_detail)
        else:
            # wq#1699: scaled, not binary, and no pri filter — scarcity shows up
            # as a lower concurrency ceiling at stage 9 instead.
            burn = (burn_rate.pools_for_model(model), allowed)
            if model not in ctx.burn_logged:
                ctx.burn_logged.add(model)
                log.emit("burn_rate", model=model,
                         reason=f"pressure tight: burn-rate ceiling {allowed}",
                         detail=burn_detail)
    if ctx.tight("cloud"):
        if sum(ctx.spawned.values()) >= tight_cfg["max_spawns_per_pass"]:
            log.emit("halt", stage="spawn_cap",
                     reason=f"tight-mode spawn cap reached ({sum(ctx.spawned.values())})")
            return "spawn_cap"
        if _tight_pri_skip(ctx, r):
            return False
    # stage 8b — weekly-budget pacing (wq#716): forward-looking, on top of the
    # governor's own reactive floor above. Never blocks a Frank-approved fire ticket.
    verdict = PACING_HOOK(cand["epri"], model, ctx.limits, ctx.pacing_cfg,
                          r["fire_exempt"])  # wq#1699: epri, not raw pri
    if verdict.action != "ok":
        log.skip(tid, lane, "budget", f"pacing_{verdict.action}", model=model,
                 **verdict.as_fields())
        handle_pacing_throttle(state, verdict, ctx.pacing_cfg, ctx.dry_run, log)
        return False

    # stage 9 — cloud concurrency ceiling; local units never count (T4).
    # wq#2426: the ceiling is config cloud_ceiling tapered by spendable budget
    # (burn_rate.budget_ceiling), on EVERY cloud admission — the burn-rate gate below
    # only runs on governor `tight`, which on zeroed floors means `critical`, and the
    # 2026-09-20/21 drain (weekly 80% -> 95%) happened entirely under `warning` at the
    # flat 10. Same pool split as that gate: the pass pools bound the whole partition
    # (halt), a narrowing from the fable pool alone skips only the fable ticket.
    _, n_cloud = ctx.unit_counts()
    model_pools = burn_rate.pools_for_model(model)
    pass_ceiling, _ = ctx.cloud_ceiling(burn_rate.PASS_POOLS)
    model_ceiling, ceiling_detail = ctx.cloud_ceiling(model_pools)
    if model_ceiling < cfg["cloud_ceiling"] and model not in ctx.ceiling_logged:
        ctx.ceiling_logged.add(model)
        log.emit("cloud_ceiling", model=model,
                 reason=f"budget-aware cloud ceiling {model_ceiling} of "
                        f"{cfg['cloud_ceiling']}", detail=ceiling_detail)
    if n_cloud >= pass_ceiling:
        log.emit("halt", stage="concurrency", reason="no_slots",
                 detail=f"{n_cloud} cloud unit(s) at ceiling {pass_ceiling} "
                        f"(config {cfg['cloud_ceiling']}); next tick retries")
        return "no_slots"
    if n_cloud >= model_ceiling:
        log.skip(tid, lane, "budget", "cloud_ceiling_cap", model=model,
                 detail=f"{n_cloud} cloud unit(s) at {model} budget ceiling "
                        f"{model_ceiling} (config {cfg['cloud_ceiling']})")
        return False
    if burn is not None and n_cloud >= burn[1]:
        # wq#1699: the burn-rate ceiling is the concurrency the budget can sustain
        # to its reset. A model on the pass pools only (every cloud model draws on
        # them) -> same halt as cloud_ceiling: dispatch_health reads no_slots as
        # healthy, and min_allowed>=1 means a worker is running. A model with its
        # own extra pool (fable) -> skip only it; its tightness may come from that
        # pool alone, so it must not halt tickets on unflagged pools behind it.
        model_pools, allowed = burn
        if model_pools == burn_rate.PASS_POOLS:
            log.emit("halt", stage="concurrency", reason="no_slots",
                     detail=f"{n_cloud} cloud unit(s) at burn-rate ceiling "
                            f"{allowed}; next tick retries")
            return "no_slots"
        log.skip(tid, lane, "budget", "burn_rate_cap", model=model,
                 detail=f"{n_cloud} cloud unit(s) at {model} burn-rate "
                        f"ceiling {allowed}")
        return False
    return True


def _spawn(ctx, r):
    """Stage 9b preflight + stage 10 spawn. Returns True iff a worker was started (or, under
    --dry-run, would have been)."""
    log, state = ctx.log, ctx.state
    cand, repo, model = r["cand"], r["repo"], r["model"]
    tid, lane = cand["id"], cand["lane"]

    # stage 9b — harness-preflight stamp (§2c): a wave must not discover a
    # permission gap three hours in when a two-minute preflight would have
    # caught it before the first spawn. Unmigrated repos (no
    # repos.d/<repo>.json) pass through unchanged — same additive-migration
    # boundary permission_profiles.py draws for the render-at-dispatch pack
    # itself. wq#1372: a stale/missing stamp (settings or preflight_cmds
    # changed since the last proof) auto-reproves right here instead of
    # stranding the lane until a human re-runs harness-preflight — see
    # handle_stale_preflight(). wq#1408: the gate check itself can raise (a
    # JSON typo in any profile fragment) — repo_gate_check caches that per
    # repo per pass so it costs one exception, not one per ticket of that repo.
    preflight_ok, preflight_reason, gate_errored = repo_gate_check(ctx.gate_cache, repo)
    if not preflight_ok:
        if gate_errored:
            handle_profile_unreadable(state, tid, lane, repo, preflight_reason,
                                       ctx.dry_run, log)
            return False
        if not handle_stale_preflight(state, tid, lane, repo, preflight_reason,
                                       ctx.dry_run, log, defer=ctx.deferred_reproves):
            return False
    elif preflight_reason:
        # wq#1409 §3.2: soft-stale — profile unchanged, settings churned.
        # Dispatch on the existing proof; say so once per repo per pass and
        # reseal so the next pass reads it as plain fresh.
        handle_soft_stale_preflight(ctx.gate_cache, tid, lane, repo, preflight_reason,
                                     ctx.dry_run, log)

    # stage 10 — spawn via the existing stack (MUST 7: router owns repo + tuple)
    member = r.get("member")  # wq#2154: the backend a group ticket was resolved to
    logged = {"member": member} if member else {}
    if ctx.dry_run:
        ctx.spawned[r["part"]] += 1  # count so dry-run reports caps honestly
        log.emit("would_spawn", ticket=tid, lane=lane, model=model, repo=repo, **logged)
        return False
    if sum(ctx.spawned.values()):
        time.sleep(random.uniform(*STAGGER))  # same stagger doctrine as spawner
    # wq#2130: a Mac-local spawn tells fworker its backend's first-frame budget and served
    # ctx (FOREMAN_FIRST_FRAME_S / FOREMAN_SERVED_CTX) — the member's, not the group's.
    # wq#2154: a resolved member also rides as the worker's model (model_override); other
    # spawns keep the 2-arg call.
    cap = ctx.capacity() if r["part"] == "local" else None
    spawn_env = cap.spawn_env(member or model) if cap is not None else {}
    kw = {k: v for k, v in (("env", spawn_env), ("model_override", member)) if v}
    unit = spawner.dispatch_one(tid, repo, **kw)
    if unit is None:
        log.emit("error", stage="spawn", ticket=tid, lane=lane,
                 reason="spawn_failed", model=model, **logged)
        ntfy(f"dispatcher: spawn failed for wq#{tid} ({lane}) — see dispatcher.log")
        return False
    ctx.spawned[r["part"]] += 1
    r["spawned"] = True  # wq#2255: the swap step counts only tickets still waiting
    ctx.units = None  # re-read: the next admission must see this worker
    state["lane_counts"][lane] = state["lane_counts"].get(lane, 0) + 1
    spawn_loop_guard.record_spawn(state, tid)  # wq#1703, before the save below
    save_state(state)  # §9: written after each spawn, atomically
    log.emit("spawn", ticket=tid, lane=lane, model=model, repo=repo, unit=unit, **logged)
    return True


def _run_partition(ctx, part, ready):
    """Admit and spawn one pool's ready tickets, in scan order. Returns why the partition
    stopped early ("kill_switch", "spawn_cap", "no_slots", "deadline",
    "lumen_run_unreachable"), or None if it ran to the end."""
    cfg, log = ctx.cfg, ctx.log
    tight_cfg = cfg["tight_mode"]
    if part == "local":
        cap = cfg.get("local_max_spawns_per_pass",
                      cfg.get("local_ceiling", concurrency.LOCAL_CEILING))
    else:
        cap = cfg["max_spawns_per_pass"]
    admit = _admit_local if part == "local" else _admit_cloud
    if part == "local":
        # wq#2130: the per-pool circuit breaker's local trip pauses this partition only
        pause = local_capacity.read_pause() if local_capacity.capacity_mode() == "live" else None
        if pause is not None:
            ctx.local_paused = True
            log.emit("halt", stage="local_pause", reason=str(pause.get("reason"))[:300],
                     until=pause.get("until"))
            return "local_paused"
    for r in ready:
        cand = r["cand"]
        tid, lane = cand["id"], cand["lane"]

        # stage 0 recheck — a mid-run DISARM/STOP halts before the next spawn (MUST 8).
        # Halts stay outside the per-ticket boundary below: a `return` there would not be
        # caught by its `except Exception` either, but keeping them out makes that moot.
        ks = kill_switch()
        if ks:
            log.emit("halt", stage="kill_switch", reason=ks)
            return "kill_switch"
        tight = ctx.tight(part)
        # tight (box pressure / fallback clamp) caps the whole pass; otherwise each pool has
        # its own cap so a busy cloud cannot eat the local lane's spawns or vice versa.
        spawned, limit = ((sum(ctx.spawned.values()), tight_cfg["max_spawns_per_pass"])
                          if tight else (ctx.spawned[part], cap))
        if spawned >= limit:
            log.emit("halt", stage="spawn_cap",
                     reason=f"per-pass {part} spawn cap reached ({spawned}/{limit}"
                            f"{', tight' if tight else ''})")
            return "spawn_cap"
        if part == "cloud" and time.monotonic() > ctx.deadline:
            log.emit("halt", stage="deadline",
                     reason=f"pass deadline {cfg.get('pass_deadline_s', PASS_DEADLINE_S)}s "
                            f"passed; cloud partition ends, next tick retries")
            return "deadline"

        # wq#1408 §3.1 (the wq#1368 class, generalized): everything below is one ticket's
        # decision. A bad archetype, an unreadable dep tag, a malformed ticket field —
        # none of it may cost any OTHER candidate its decision this pass. Only
        # KeyboardInterrupt/SystemExit escape; everything else is quarantined.
        try:
            # spawns earlier in this pass may have used up the lane's daily allowance that
            # the gate saw as open
            policy = r["policy"]
            if ctx.state["lane_counts"].get(lane, 0) >= policy["max_per_day"]:
                ctx.lane_capped[part] += 1
                log.skip(tid, lane, "lane_policy", "lane_cap",
                         detail=f"max_per_day={policy['max_per_day']} reached")
                continue
            if ctx.units is None:
                try:
                    ctx.refresh_units()
                except concurrency.LumenRunError as e:
                    log.emit("halt", stage="concurrency", reason="lumen_run_unreachable",
                             detail=str(e))  # fail toward under-subscription
                    return "lumen_run_unreachable"
            verdict = admit(ctx, r)
            if isinstance(verdict, str):
                return verdict
            if verdict:
                _spawn(ctx, r)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001 — wq#1408 §3.1: quarantine boundary
            log.skip(tid, lane, "unhandled", "quarantined", detail=repr(e)[:300])
            ctx.quarantined.append(tid)
    return None


def _do_pass(cfg, log, state, dry_run, deferred=None, config_sha=None, gate_salt=None):
    candidates = scan_candidates(cfg, log)
    scanned = len(candidates)
    tight_cfg = cfg["tight_mode"]
    tight = False
    # wq#1408 §3.1: these three preamble calls run once per pass, before any ticket is
    # decided — an exception here used to abort the pass before a single candidate got
    # a decision (no pass_end, invisible to the health classifier). Degrade to each
    # call's documented safe default instead: no stuck set, psi=ok, pacing's own
    # conservative DEFAULT_CONFIG.
    try:
        stuck = runaway.stuck_ticket_ids()  # wq#718: one scan per pass, not per-candidate
    except Exception as e:  # noqa: BLE001 — see docstring above
        log.emit("error", stage="runaway", reason="stuck_ticket_ids_failed",
                 detail=repr(e)[:300])
        stuck = {}
    try:
        # wq#1415: ditto — a SpawnRefused loop never writes attempt-N.json, so it never
        # shows up in `stuck` above; degrade the same way (no holds this pass) on error.
        # step 8: state["harness_defect_seen"] is the edit witness (mutated in place, saved
        # below when it changed); a failure.json older than an edit no longer holds.
        seen_before = json.dumps(state.get("harness_defect_seen"), sort_keys=True)
        harness_defects = runaway.harness_defect_ticket_ids(
            state.setdefault("harness_defect_seen", {}))
        if not dry_run and json.dumps(state["harness_defect_seen"],
                                      sort_keys=True) != seen_before:
            save_state(state)
    except Exception as e:  # noqa: BLE001 — see docstring above
        log.emit("error", stage="runaway", reason="harness_defect_ticket_ids_failed",
                 detail=repr(e)[:300])
        harness_defects = {}

    # wq#844: PSI memory pressure — real backpressure from the box, not budget guessing.
    # Checked once per pass (PSI avg60 is already smoothed).
    try:
        psi_verdict, psi_reason = psi_pressure.check(tight_cfg)
    except Exception as e:  # noqa: BLE001 — see docstring above
        log.emit("error", stage="psi_pressure", reason="psi_check_failed",
                 detail=repr(e)[:300])
        psi_verdict, psi_reason = "ok", "psi_check_error"
    if psi_verdict == "deny":
        log.emit("halt", stage="psi_pressure", reason=psi_reason)
        ntfy(f"dispatcher: PSI hard stop — {psi_reason}", priority="high",
             title="dispatcher PSI halt")
        return {"candidates": len(candidates), "spawned": 0, "tight": False,
                "quarantined": 0, "psi_halt": psi_reason}
    if psi_verdict == "tight" and not tight:
        tight = True
        log.emit("tight_mode", reason=psi_reason,
                 detail=f"PSI pressure: max {tight_cfg['max_spawns_per_pass']} "
                        f"spawn(s), epri<={tight_cfg['min_pri']} only")

    # wq#2631/wq#2647: PSI CPU pressure — mirrors the wq#844 memory-PSI block above,
    # same shared admission_gate.check() a subprocess caller (dispatch-batch) uses.
    try:
        cpu_verdict, cpu_reason = cpu_pressure.check(tight_cfg)
    except Exception as e:  # noqa: BLE001 — see docstring above
        log.emit("error", stage="cpu_pressure", reason="cpu_check_failed",
                 detail=repr(e)[:300])
        cpu_verdict, cpu_reason = "ok", "cpu_check_error"
    if cpu_verdict == "deny":
        log.emit("halt", stage="cpu_pressure", reason=cpu_reason)
        ntfy(f"dispatcher: CPU PSI hard stop — {cpu_reason}", priority="high",
             title="dispatcher CPU halt")
        return {"candidates": len(candidates), "spawned": 0, "tight": False,
                "quarantined": 0, "cpu_halt": cpu_reason}
    if cpu_verdict == "tight" and not tight:
        tight = True
        log.emit("tight_mode", reason=cpu_reason,
                 detail=f"CPU pressure: max {tight_cfg['max_spawns_per_pass']} "
                        f"spawn(s), epri<={tight_cfg['min_pri']} only")

    try:
        pacing_cfg, pacing_warn = pacing.load_config()  # wq#716: forward-looking layer §6
    except Exception as e:  # noqa: BLE001 — see docstring above
        log.emit("error", stage="pacing", reason="pacing_load_failed",
                 detail=repr(e)[:300])
        pacing_cfg, pacing_warn = dict(pacing.DEFAULT_CONFIG), None
    if pacing_warn:
        log.emit("info", stage="pacing", reason="pacing_config_invalid", detail=pacing_warn)
    limits = governor_limits()

    # wq#1669/wq#1699: surface the usage API's severity labels every pass. router
    # grades them (warning is advisory on a zero-floor pool, critical engages the
    # burn-rate controller), so "why is nothing throttling at 86%" must stay answerable
    # from this log alone.
    hot = sorted(p for p, b in limits.items()
                 if isinstance(b, dict) and b.get("severity") in ("warning", "critical"))
    if hot:
        log.emit("info", stage="budget", reason="api_severity",
                 detail=",".join(f"{p}={limits[p]['severity']}" for p in hot))

    # wq#1669/wq#1699: burn-rate controller inputs. When governor pressure is tight the
    # controller turns budget-remaining vs time-to-reset into a concurrency ceiling,
    # enforced at stage 9 against the same live unit list as cloud_ceiling. Missing
    # percent/resets_at -> None -> the old binary tight_mode clamp.
    burn_cfg = burn_rate.merge_config(cfg.get("burn_rate"))
    burn_obs = burn_rate.load_observed()
    burn_cache = {}   # pools tuple -> (allowed_concurrent | None, detail)
    burn_logged = set()
    # wq#1889: one frank_active read for the whole pass, surfaced on pass_end so
    # "why did the ceiling drop this pass" is answerable from the log alone — the
    # per-pool ceilings above already apply it internally (burn_rate.allowed_concurrent).
    frank_is_active, frank_active_detail = burn_rate.frank_active(datetime.now(timezone.utc))

    ctx = _PassCtx(cfg, log, state, dry_run)
    ctx.stuck, ctx.harness_defects, ctx.psi_tight = stuck, harness_defects, tight
    ctx.limits, ctx.pacing_cfg, ctx.burn_cfg, ctx.burn_obs = limits, pacing_cfg, burn_cfg, burn_obs
    if deferred is not None:
        ctx.deferred_reproves = deferred
    # step 11 salt: the config BLOB sha (content), not the commit sha — see config_blob_sha().
    ctx.gate_salt = gate_salt if gate_salt is not None else (config_sha or "")
    candidates, n_parked = _apply_parking(ctx, candidates)  # step 11: open minus parked

    if not hygiene_legacy():
        try:
            sweep_human_lanes(cfg, log)
        except Exception as e:  # noqa: BLE001 — the liveness sweep only ever closes; never load-bearing
            log.emit("error", stage="liveness", reason="human_lane_sweep_failed",
                     detail=repr(e)[:300])

    # gate: every pool-independent stage, once per candidate, in scan order
    ready = {"local": [], "cloud": []}
    halted = False
    for cand in candidates:
        ks = kill_switch()  # stage 0
        if ks:
            log.emit("halt", stage="kill_switch", reason=ks)
            halted = True
            break
        try:
            r = _gate_candidate(ctx, cand)
        except concurrency.LumenRunError as e:
            log.emit("halt", stage="concurrency", reason="lumen_run_unreachable",
                     detail=str(e))  # fail toward under-subscription
            halted = True
            break
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001 — wq#1408 §3.1: quarantine boundary
            log.skip(cand["id"], cand["lane"], "unhandled", "quarantined", detail=repr(e)[:300])
            ctx.quarantined.append(cand["id"])
            continue
        if r:
            ready[r["part"]].append(r)

    # admit: local first — free compute is never queued behind a budget decision, and a
    # cloud stop cannot precede a local ticket
    stopped = {"local": None, "cloud": None}
    for part in () if halted else ("local", "cloud"):
        stopped[part] = _run_partition(ctx, part, ready[part])
        if stopped[part] in ("kill_switch", "lumen_run_unreachable"):
            break

    if not halted:
        _node_power_step(ctx, ready)
        _model_swap_step(ctx, ready)

    # step 8 (F7): dispatchable is the READY set, not candidates minus a list of reasons that
    # has to learn every new gate. A ticket that passed every gate but lost its lane's daily
    # allowance mid-run is explained, so it is not counted.
    dispatchable_pool = {p: len(ready[p]) - ctx.lane_capped[p] for p in ready}
    return {"candidates": scanned, "spawned": sum(ctx.spawned.values()),
            # step 11: `evaluated` is what the gates actually ran on = scanned - parked
            "evaluated": len(candidates), "parked": n_parked,
            "tight": ctx.psi_tight or ctx.cloud_tight,
            "burn_ceiling": {",".join(k): v[0] for k, v in ctx.burn_cache.items()},
            # wq#2426: the budget-aware ceiling(s) stage 9 actually enforced this pass
            "cloud_ceiling": {",".join(k): v[0] for k, v in ctx.ceiling_cache.items()},
            "frank_active": frank_is_active, "frank_active_detail": frank_active_detail,
            "quarantined": len(ctx.quarantined),
            "dispatchable": sum(dispatchable_pool.values()),  # old readers; = the per-pool sum
            "ready_local": len(ready["local"]), "ready_cloud": len(ready["cloud"]),
            "lint_scanned": ctx.lint_scanned, "lint_flagged": ctx.lint_flagged,
            # wq#2088: per-pool view, so a local stall cannot hide behind a healthy cloud
            "dispatchable_local": dispatchable_pool["local"],
            "dispatchable_cloud": dispatchable_pool["cloud"],
            "spawned_local": ctx.spawned["local"], "spawned_cloud": ctx.spawned["cloud"],
            "decomposer_spawned": ctx.decomposer_spawned,  # wq#2516

            "local_slots_full": ctx.local_slots_full,
            "local_paused": ctx.local_paused,
            "stopped_local_reason": stopped["local"],
            "stopped_cloud_reason": stopped["cloud"]}


def run_pass(dry_run=False):
    pass_id = ("fdisp-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
               + f"-{os.getpid()}")
    log = DecisionLog(LOG_PATH, pass_id)

    ks = kill_switch()  # stage 0 (MUST 8)
    if ks:
        log.emit("halt", stage="kill_switch", reason=ks)
        return 0

    try:
        cfg, lane_warnings, config_sha = load_config()  # §4: structural errors fail
        # closed; wq#824: per-lane errors are soft (lane disabled, pass continues)
    except ConfigError as e:
        log.emit("error", stage="config", reason=str(e))
        ntfy(f"dispatcher: invalid config — pass aborted: {e}")
        return 1
    for w in lane_warnings:
        log.emit("error", stage="config_lane", reason=w)
    if lane_warnings:
        bad = ", ".join(w.split(".")[0].replace("lanes", "").strip(".")
                        for w in lane_warnings)
        ntfy(f"dispatcher: bad lane config ({bad}) — lane(s) disabled, "
             f"other lanes proceeding. Details: {'; '.join(lane_warnings)}",
             priority="high", title="dispatcher config warning")
    # stage 1. Step 8: a --dry-run has zero side effects, so it takes no lock — it neither
    # blocks a real pass (lock_busy) nor is refused by one.
    if dry_run and not hygiene_legacy():
        ok, holder = True, None
    else:
        ok, holder = slock_acquire()
    if not ok:
        log.emit("halt", stage="lock", reason="lock_busy")
        return 0

    rc = 0
    deferred = []
    try:

        # stage 0.5 (wq#718): dead-man's switch. wq#1408: moved inside this try — it
        # used to run before config load with no boundary of its own, so an exception
        # out of check_and_trip (a bad runs_root read, a malformed attempt file) was a
        # bare traceback: no error line in dispatcher.log, invisible even to the health
        # classifier. Now it's caught by the same pass boundary as everything else.
        tripped, breaker_stats = circuit_breaker.check_and_trip(dry_run=dry_run)
        if tripped:
            reason = (f"failure rate {breaker_stats['rate']:.0%} over last "
                      f"{breaker_stats['window_minutes']}m "
                      f"({breaker_stats['failures']}/{breaker_stats['total']} attempts)")
            log.emit("halt", stage="circuit_breaker", reason=reason, **breaker_stats)
            return 0
        # wq#2130: the local pool trips on its own window and only pauses the local partition
        local_tripped, local_stats = circuit_breaker.check_and_trip_local(dry_run=dry_run)
        if local_tripped:
            log.emit("info", stage="circuit_breaker_local", reason="local_pause_written",
                     **local_stats)
        gate_salt = config_blob_sha(CONFIG_PATH)
        log.emit("pass_start", dry_run=dry_run, config_path=CONFIG_PATH,
                 config_sha=config_sha, config_blob=gate_salt)
        state = load_state()
        summary = _do_pass(cfg, log, state, dry_run, deferred, config_sha, gate_salt)
        log.emit("pass_end", **summary)
    except Exception as e:  # noqa: BLE001 — pass boundary: log + ntfy, never silent
        log.emit("error", stage="pass", reason=f"unhandled: {e!r}")
        ntfy(f"dispatcher: pass {pass_id} crashed: {e!r}")
        rc = 1
    finally:
        if holder:
            slock_release(holder)
        # step 8: stale-stamp reproves deferred by _spawn run here, after the lock is free —
        # a 420 s ladder inside it made every other pass read lock_busy. The pass's decisions
        # are already logged; this only refreshes stamps for the NEXT pass.
        for tid, lane, repo, reason in deferred:
            try:
                handle_stale_preflight(load_state(), tid, lane, repo, reason, dry_run, log,
                                       post_lock=True)
            except Exception as e:  # noqa: BLE001 — never lose the Langfuse emit below
                log.emit("error", stage="preflight", reason="deferred_reprove_failed",
                         detail=repr(e)[:300])
        try:
            shipped = LANGFUSE_EMIT(pass_id, log.events)
            if shipped is None:
                log.emit("info", stage="langfuse", reason="langfuse_disabled",
                         detail="no keys at LANGFUSE_ENV — log-only degradation")
        except Exception as e:  # noqa: BLE001 — best-effort by contract (§8)
            log.emit("error", stage="langfuse", reason="langfuse_emit_failed",
                     detail=repr(e))
    return rc


_terminating = False


def _term_to_exit(signum, _frame):
    """wq#2080: systemd's TimeoutStartSec sends SIGTERM, which by default kills Python
    without running `finally` — the slock leaked for its full 30m TTL. SystemExit unwinds
    through run_pass's release.

    wq#2084: router.wq_show/assign also raise SystemExit for not-found, and the scan-stage
    handlers swallow it; they re-raise when _terminating is set so the signal isn't
    mistaken for a vanished ticket."""
    global _terminating
    _terminating = True
    raise SystemExit(128 + signum)


def main(argv=None):
    ap = argparse.ArgumentParser(description="foreman auto-dispatcher (wq#715 AUTO-3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one dispatch pass and exit")
    r.add_argument("--once", action="store_true",
                   help="documentational: a pass is always one-shot")
    r.add_argument("--dry-run", action="store_true",
                   help="decide + log would_spawn; zero side effects")
    a = ap.parse_args(argv)
    global _terminating
    _terminating = False
    signal.signal(signal.SIGTERM, _term_to_exit)
    return run_pass(dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
