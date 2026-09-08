#!/usr/bin/env python3
"""self_heal.py — wq self-heal daemon (wq#725 AUTO-7). Spec: the ticket's FIX §2.

PROBLEM (observed 2026-08-20, wq#704/705): fworker correctly exhausts MAX_ATTEMPTS and
calls wq_block(), but a blocked ticket whose spawn ENVIRONMENT was broken (wrong --repo,
a stale worktree from a prior crashed attempt) sits in the exact same `blocked` state as
a ticket the worker genuinely could not do — no distinction, no auto-recovery. A human
has to notice and manually `wq release` + re-dispatch.

FIX: harness/fworker.py's handle_failure() now prefixes every wq_block() reason with one
of three classes (classify_block_reason(), same module):
  INFRA_CRASH:   spawn/process environment broke, work never really attempted
  LIMIT_HIT:     turn or budget ceiling — a resourcing call, not a self-heal one
  WORK_BLOCKED:  the worker/model decided it can't do the work, or an unrecognized shape
  HARNESS_DEFECT (wq#1107): our own configuration refused a tool call the ticket needed
This daemon scans `wq list --status blocked`, and for every INFRA_CRASH ticket:
  - if fworker's own attempt-N.json count for the ticket is under MAX_HEAL_ATTEMPTS AND
    this occurrence of the block is under STALE_AFTER_MIN old -> `wq release` it (the
    EXISTING auto-dispatcher, foreman_dispatcher.py, picks a released/open ticket back up
    on its own next pass — this daemon never re-implements spawn; DESIGN.md's division of
    labor applies here too).
  - otherwise (attempts exhausted, or the block has been sitting INFRA_CRASH for too long
    despite releases) -> ntfy Frank (deduped) and leave it blocked.
LIMIT_HIT, WORK_BLOCKED and HARNESS_DEFECT tickets are NEVER released by this daemon — no
override, no exception; a block reason wq doesn't recognize (predates this feature, or
Frank blocked it by hand) is treated the same conservative way as WORK_BLOCKED.
HARNESS_DEFECT is listed in BLOCK_PREFIX_RE rather than left to that fallback so the
class shows up as itself in `self_heal status`: releasing it would only re-run the ticket
into the same refusal, so the release has to wait on a harness change landing first.

wq#741 gave wq dedicated `block_reason`/`blocked_at` columns on tasks (ADDITIVE, same
ALTER-TABLE-if-missing pattern as lease_expires/subsystems/gate) — cmd_block() writes both
alongside notes. This daemon reads `blocked_at` straight from `wq show` for the age check,
so a re-block naturally starts a fresh occurrence (wq clears both columns on release/done).
Tickets blocked before wq#741 shipped may still carry the class prefix only in `notes` —
classify_block() falls back to that so an in-flight INFRA_CRASH isn't stranded across the
upgrade. state.json now holds only the ntfy dedup ledger (`stale_ntfy`), not a first-seen
ledger. The attempt cap reads fworker's own attempt-N.json files directly (harness/
fworker.py's run_dir/next_attempt_n) rather than keeping a second counter that could drift
out of sync with the harness's real attempt count.

wq#1012 (2026-08-27): the dispatcher-not-dispatching watchdog is NO LONGER run here.
It was duplicated — this piggybacked dispatcher_health.check_dispatcher_health() call
AND the standalone dispatch_health.py (lumen-foreman-health.timer, 5-min) both alarmed
the same condition on out-of-phase cadences (R2 T5 / R3 wq#1012). Single owner is now
dispatch_health.py: it writes health_state.json, runs the config-lint, and is the only
DISPATCHER-STALLED pager. Do NOT re-add a check_dispatcher_health() call here.
dispatcher_health.py was unreferenced and has been removed (wq#1416).

CLI:  self_heal.py run --once [--dry-run]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, DISPATCHER_DIR)
import vlogs_mirror  # noqa: E402

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
STATE_PATH = os.environ.get("FOREMAN_SELFHEAL_STATE",
                            os.path.join(FOREMAN_ROOT, "self_heal", "state.json"))
LOG_PATH = os.environ.get("FOREMAN_SELFHEAL_LOG",
                          os.path.join(FOREMAN_ROOT, "self_heal.log"))
RUNS_ROOT = os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(FOREMAN_ROOT, "_runs"))
DISARM_PATH = os.environ.get("FOREMAN_DISPATCH_DISARM",
                             "/home/lumen/autonomy/DISPATCH_DISARM")
STOP_PATH = os.environ.get("FOREMAN_STOP_FILE", os.path.join(FOREMAN_ROOT, "STOP"))
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
SLOCK_BIN = os.environ.get("FOREMAN_SLOCK_BIN", "/home/lumen/bin/slock")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")

SLOCK_SCOPE = "foreman-selfheal"
SLOCK_TTL_MIN = "10"
STALE_AFTER_MIN = int(os.environ.get("FOREMAN_SELFHEAL_STALE_MIN", "30"))
MAX_HEAL_ATTEMPTS = int(os.environ.get("FOREMAN_SELFHEAL_MAX_ATTEMPTS", "5"))
STALE_NTFY_DEDUP_H = 6  # a still-stuck ticket only re-pings Frank every 6h, not every pass

BLOCK_PREFIX_RE = re.compile(
    r"^(INFRA_CRASH|WORK_BLOCKED|LIMIT_HIT|HARNESS_DEFECT):\s*(.*)$", re.S)
# wq#1330: fworker's block reason first line is now "<triage>: <one-line>; see
# <failure.json path>" (WORKER_EXEC_CAPABILITY_GAP.md §4.2). The block CLASS this
# daemon acts on moved into the failure.json the reason points at; the prefix map
# below is the conservative fallback when that file is missing/unreadable.
TRIAGE_PREFIX_RE = re.compile(
    r"^(CAPABILITY_DENIED|HARNESS_DEFECT|GENUINE_BUG|REAL_BLOCK|LIMIT|UNKNOWN):"
    r"\s*(.*)$", re.S)
TRIAGE_FALLBACK_CLASS = {
    "CAPABILITY_DENIED": "HARNESS_DEFECT",  # our pack refused the call — wq#1107 bucket
    "HARNESS_DEFECT": "HARNESS_DEFECT",
    "LIMIT": "LIMIT_HIT",
    # GENUINE_BUG / REAL_BLOCK / UNKNOWN: conservative, never auto-released
}
FAILURE_JSON_RE = re.compile(r"see\s+(\S+failure\.json)")
# wq list line: `[ 12] lane p1 ep0 blocked  title...` (ep tag only when it differs)
LIST_LINE_RE = re.compile(r"^\[\s*(\d+)\]\s+(\S+)\s+p(\d+)(?:\s+ep(\d+))?\s+(\S+)")
ATTEMPT_FILE_RE = re.compile(r"^attempt-(\d+)\.json$")


# ---- decision log (same shape as foreman_dispatcher.DecisionLog) ---------------------

class DecisionLog:
    def __init__(self, path, pass_id):
        self.path, self.pass_id = path, pass_id
        self._file_broken = False

    def emit(self, event, **fields):
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "pass_id": self.pass_id, "event": event}
        rec.update(fields)
        line = json.dumps(rec)
        print(line, flush=True)
        try:
            d = os.path.dirname(self.path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.path, "a") as f:
                f.write(line + "\n")
        except OSError as e:
            if not self._file_broken:
                self._file_broken = True
                print(f"[self_heal] WARN cannot append {self.path}: {e}",
                      file=sys.stderr, flush=True)


def _utcnow():
    return datetime.now(timezone.utc)


# ---- state.json: {"stale_ntfy": {"<id>": iso}} — first-seen tracking now lives in wq's own
# blocked_at column (wq#741); this file is just the "don't re-ping Frank every pass" ledger.

def load_state():
    fresh = {"stale_ntfy": {}}
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return fresh
    if not isinstance(st, dict):
        return fresh
    stale = st.get("stale_ntfy")
    return {"stale_ntfy": stale if isinstance(stale, dict) else {}}


def save_state(st):
    d = os.path.dirname(STATE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


# ---- kill switches / slock / ntfy (mirrors foreman_dispatcher.py) --------------------

def kill_switch():
    if os.path.exists(DISARM_PATH):
        return f"DISPATCH_DISARM present ({DISARM_PATH})"
    if os.path.exists(STOP_PATH):
        return f"foreman STOP present ({STOP_PATH})"
    return None


def slock_acquire():
    holder = f"foreman-selfheal:{os.getpid()}"
    try:
        p = subprocess.run([SLOCK_BIN, "acquire", SLOCK_SCOPE, "--ttl", SLOCK_TTL_MIN,
                            "--holder", holder], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False, holder
    return p.returncode == 0, holder


def slock_release(holder):
    try:
        subprocess.run([SLOCK_BIN, "release", SLOCK_SCOPE, "--holder", holder],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def ntfy(msg, priority="default", title="foreman-selfheal"):
    vlogs_mirror.mirror("self_heal", title, priority, msg)
    try:
        subprocess.run([NTFY_BIN, "-p", priority, "-T", title, msg],
                       capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---- wq -------------------------------------------------------------------------------

def list_blocked():
    try:
        p = subprocess.run([WQ_BIN, "list", "--status", "blocked"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"wq list failed: {e}"
    if p.returncode != 0:
        return None, f"wq list rc={p.returncode}: {p.stderr.strip()}"
    ids = []
    for line in p.stdout.splitlines():
        m = LIST_LINE_RE.match(line)
        if not m:
            continue
        tid, _lane, _pri, _ep, status = m.groups()
        if status == "blocked":
            ids.append(int(tid))
    return ids, None


def wq_show(tid):
    p = subprocess.run([WQ_BIN, "show", str(tid)], capture_output=True, text=True, timeout=30)
    out = {}
    for line in p.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def wq_release(tid):
    p = subprocess.run([WQ_BIN, "release", str(tid)], capture_output=True, text=True, timeout=30)
    return p.returncode == 0, p.stdout + p.stderr


def classify_block(ticket):
    """(block_class, detail) parsed from a ticket's block_reason (wq#741 column); falls back
    to notes for tickets blocked before that column existed, so an in-flight INFRA_CRASH
    isn't stranded across the upgrade. (None, reason) when the reason predates the prefix
    convention entirely or was written by hand (Frank) -> treated the same conservative way
    as WORK_BLOCKED by the caller, never auto-released."""
    # `wq show`'s generic key:value dump prints a NULL column as the literal string "None",
    # so that has to be treated as empty before the `or` fallback even sees it.
    reason = ticket.get("block_reason")
    if not reason or reason == "None":
        reason = ticket.get("notes") or ""
    if reason == "None":
        reason = ""
    m = BLOCK_PREFIX_RE.match(reason)
    if m:
        return m.group(1), m.group(2)
    # wq#1330 triage-led reason: the authoritative block_class is in the failure.json
    # the reason cites (so an INFRA_CRASH-classified KILLED/OOM keeps auto-healing);
    # fall back to the static triage map, then to unmarked-conservative.
    t = TRIAGE_PREFIX_RE.match(reason)
    if not t:
        return None, reason
    fj = FAILURE_JSON_RE.search(reason)
    if fj:
        try:
            with open(fj.group(1)) as f:
                block_class = json.load(f).get("block_class")
            if block_class in ("INFRA_CRASH", "WORK_BLOCKED", "LIMIT_HIT",
                               "HARNESS_DEFECT"):
                return block_class, t.group(2)
        except (OSError, ValueError):
            pass
    return TRIAGE_FALLBACK_CLASS.get(t.group(1)), t.group(2)


def parse_blocked_at(raw):
    """wq's `now()` (/home/lumen/bin/wq) writes naive-UTC '%Y-%m-%d %H:%M:%S'. Returns None
    for a missing/legacy column (pre-wq#741 blocks, or a NULL `wq show` prints as "None")."""
    if not raw or raw == "None":
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def attempt_count(tid):
    """Read fworker's own attempt-N.json count (harness/fworker.py run_dir/
    next_attempt_n) instead of a second counter this daemon would have to keep in sync."""
    d = os.path.join(RUNS_ROOT, f"wq{tid}")
    try:
        return len([f for f in os.listdir(d) if ATTEMPT_FILE_RE.match(f)])
    except OSError:
        return 0


# ---- the pass ---------------------------------------------------------------------------

def _maybe_ntfy_stale(state, tid, msg, dry_run, log):
    now = _utcnow()
    last = state["stale_ntfy"].get(str(tid))
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(hours=STALE_NTFY_DEDUP_H):
                return
        except ValueError:
            pass
    if dry_run:
        log.emit("would_ntfy", ticket=tid, reason="stale_infra_crash")
        return
    ntfy(f"wq#{tid}: {msg} — self-heal will not auto-release further; needs a human look.",
         priority="high", title="foreman self-heal: stuck INFRA_CRASH")
    state["stale_ntfy"][str(tid)] = now.isoformat(timespec="seconds")


def _do_pass(dry_run, log):
    blocked_ids, err = list_blocked()
    if err:
        log.emit("error", stage="scan", reason=err)
        return {"blocked": 0, "healed": 0}

    state = load_state()
    seen = set()
    healed = 0

    for tid in blocked_ids:
        seen.add(str(tid))
        ks = kill_switch()
        if ks:
            log.emit("halt", stage="kill_switch", reason=ks)
            break

        t = wq_show(tid)
        if t.get("status") != "blocked":  # vanished / raced back to another state
            log.emit("skip", ticket=tid, reason="not_blocked", detail=t.get("status"))
            continue

        klass, detail = classify_block(t)
        if klass != "INFRA_CRASH":
            log.emit("skip", ticket=tid, reason="not_infra_crash",
                     klass=klass or "unmarked")
            continue

        attempts = attempt_count(tid)
        if attempts >= MAX_HEAL_ATTEMPTS:
            log.emit("skip", ticket=tid, reason="heal_attempts_exhausted",
                     attempts=attempts, max=MAX_HEAL_ATTEMPTS)
            _maybe_ntfy_stale(state, tid,
                              f"{attempts} attempts already (max {MAX_HEAL_ATTEMPTS})",
                              dry_run, log)
            continue

        # missing blocked_at (a block from before wq#741 added the column) reads as just
        # blocked now — never stale — rather than stranding a legacy INFRA_CRASH forever.
        first_seen = parse_blocked_at(t.get("blocked_at")) or _utcnow()
        age_min = (_utcnow() - first_seen).total_seconds() / 60
        if age_min >= STALE_AFTER_MIN:
            log.emit("skip", ticket=tid, reason="stale_infra_crash",
                     age_min=round(age_min, 1), stale_after_min=STALE_AFTER_MIN)
            _maybe_ntfy_stale(state, tid, f"blocked {age_min:.0f}m ago and still "
                              f"INFRA_CRASH", dry_run, log)
            continue

        if dry_run:
            log.emit("would_heal", ticket=tid, attempts=attempts, age_min=round(age_min, 1))
            continue

        ok, out = wq_release(tid)
        if not ok:
            log.emit("error", stage="heal", ticket=tid, reason=f"wq release failed: {out}")
            continue
        healed += 1
        log.emit("heal", ticket=tid, attempts=attempts, age_min=round(age_min, 1),
                 detail=detail)

    if not dry_run:
        for tid in list(state["stale_ntfy"]):
            if tid not in seen:  # left blocked (healed, closed, or hand-released)
                state["stale_ntfy"].pop(tid, None)
        save_state(state)

    return {"blocked": len(blocked_ids), "healed": healed}


def run_pass(dry_run=False):
    pass_id = ("fheal-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
               + f"-{os.getpid()}")
    log = DecisionLog(LOG_PATH, pass_id)

    ks = kill_switch()
    if ks:
        log.emit("halt", stage="kill_switch", reason=ks)
        return 0
    ok, holder = slock_acquire()
    if not ok:
        log.emit("halt", stage="lock", reason="lock_busy")
        return 0

    rc = 0
    try:
        log.emit("pass_start", dry_run=dry_run)
        summary = _do_pass(dry_run, log)
        log.emit("pass_end", **summary)
    except Exception as e:  # noqa: BLE001 — pass boundary: log + ntfy, never silent
        log.emit("error", stage="pass", reason=f"unhandled: {e!r}")
        ntfy(f"self-heal: pass {pass_id} crashed: {e!r}", priority="high")
        rc = 1
    finally:
        slock_release(holder)
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="wq self-heal daemon (wq#725 AUTO-7)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one self-heal pass and exit")
    r.add_argument("--once", action="store_true",
                   help="documentational: a pass is always one-shot")
    r.add_argument("--dry-run", action="store_true",
                   help="decide + log would_heal/would_ntfy; zero side effects")
    a = ap.parse_args(argv)
    return run_pass(dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
