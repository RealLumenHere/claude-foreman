#!/usr/bin/env python3
"""dispatch_janitor.py — the 15-minute housekeeper for parked and blocked tickets
(DISPATCH_REBUILD_SPEC step 11, wq#2134).

Parking (gate_store.py) stops the dispatcher re-deriving a verdict nobody can change; someone
still has to change it. The janitor is that someone, for the cases that need no human:

  (a) release      `blocked` tickets whose cause is RUNG_FAULT (the backend failed, not the ticket)
                   once that backend is healthy — self_heal's own health rule, but in BATCHES of at
                   most live slots x 2 per run, so a recovered rung is refilled, not stampeded.
  (b) relane       a parked repo_mismatch / lane_unmapped ticket whose title carries an
                   unambiguous repo marker (router.title_repo_marker) and maps to exactly one
                   enabled non-human lane -> `wq edit --lane`, then unpark.
  (c) harness holds  a HARNESS_DEFECT failure.json that is stale — the ticket was edited since it
                   was written, or the paths it named are now granted — is archived to
                   failure.json.cleared so the hold does not depend on the dispatcher re-proving it.
  (d) spawn_loop   a ticket the spawn-loop guard has tripped twice in 24 h is blocked with
                   `SPAWN_LOOP: <last release note>` (the dispatcher does this in-pass; this is the
                   backstop for a pass that is not running).
  (e) digest       ONE aq row per parked cause per UTC day listing the tickets, replacing a page per
                   ticket per pass. Self-resolving causes (lane_cap, held, deps_unmet) are not
                   digested.

It NEVER touches a secrets-denied ticket (capability_lint reports `refused` for it): none of
(a)-(d) acts on one, and the secret wall stays fail-closed in the dispatcher. Kill switches
(DISPATCH_DISARM / STOP) halt it like self_heal. `--dry-run` decides and logs, and writes nothing.

Every action is fail-open per ticket and per section: one bad ticket or a sqlite hiccup costs that
ticket / section its run, never the rest. Rollback: FOREMAN_GATE_STORE=off empties (b) and (e)'s
input; leaving lumen-foreman-janitor.timer disabled is the whole off switch.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import capability_lint  # noqa: E402
import foreman_dispatcher as fd  # noqa: E402 — load_config(), HUMAN_LANES
import gate_store  # noqa: E402
import local_capacity  # noqa: E402
import router  # noqa: E402
import runaway  # noqa: E402
import self_heal  # noqa: E402
import spawn_loop_guard  # noqa: E402

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
STATE_PATH = os.environ.get("FOREMAN_JANITOR_STATE",
                            os.path.join(FOREMAN_ROOT, "dispatch_janitor", "state.json"))
LOG_PATH = os.environ.get("FOREMAN_JANITOR_LOG", os.path.join(FOREMAN_ROOT, "dispatch_janitor.log"))
AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq")
AQ_SOURCE = "foreman-janitor"
SLOCK_SCOPE = "foreman-janitor"
SLOCK_TTL_MIN = "10"
BATCH_SLOTS_FACTOR = 2      # (a): release at most live slots x 2 per run
DIGEST_LIST_MAX = 25
# the parked causes that clear themselves on the natural recheck — a daily row for them is noise
SELF_RESOLVING = frozenset({"lane_cap", "held", "deps_unmet"})
RELANE_REASONS = ("repo_mismatch", "lane_unmapped")


def _utcnow():
    return datetime.now(timezone.utc)


def load_state():
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {"digest": {}}
    digest = st.get("digest") if isinstance(st, dict) else None
    return {"digest": digest if isinstance(digest, dict) else {}}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_PATH)


def slock_acquire():
    holder = f"foreman-janitor:{os.getpid()}"
    try:
        p = subprocess.run([self_heal.SLOCK_BIN, "acquire", SLOCK_SCOPE, "--ttl", SLOCK_TTL_MIN,
                            "--holder", holder], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False, holder
    return p.returncode == 0, holder


def slock_release(holder):
    try:
        subprocess.run([self_heal.SLOCK_BIN, "release", SLOCK_SCOPE, "--holder", holder],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def secret_denied(t):
    """True when the ticket's own text hits the secret wall (capability_lint `refused`), or when
    that cannot be determined — the janitor never acts on a ticket it cannot clear. With the
    lint switched off (FOREMAN_NEEDS_LINT_MODE=off) there is no wall to consult."""
    if not capability_lint.enabled():
        return False
    try:
        derived = capability_lint.derive(t)
    except Exception:  # noqa: BLE001 — fail closed: an underivable ticket is left alone
        return True
    return bool(derived is not None and derived.refused)


# ---- (a) release RUNG_FAULT blocks, in batches -------------------------------------------

def live_slots(backends=None):
    """Total slots of the local backends whose health probe answers and that are not cooling
    down. 0 when none is live — then nothing is released."""
    backends = self_heal.local_backends() if backends is None else backends
    return local_capacity.LocalCapacity(backends, running=lambda: []).live_slots()


def release_rung_faults(log, dry_run):
    """(released, held_back). Oldest block first, at most live_slots x 2 per run."""
    ids, err = self_heal.list_blocked()
    if err:
        log.emit("error", stage="release", reason=err)
        return 0, 0
    batch = live_slots() * BATCH_SLOTS_FACTOR
    due = []
    for tid in ids:
        t = self_heal.wq_show(tid)
        if t.get("status") != "blocked":
            continue
        klass, detail = self_heal.classify_block(t)
        if klass != "RUNG_FAULT":
            continue
        if secret_denied(t):
            log.emit("skip", stage="release", ticket=tid, reason="secret_denied")
            continue
        if self_heal.attempt_count(tid, charged_only=True) >= self_heal.MAX_HEAL_ATTEMPTS:
            log.emit("skip", stage="release", ticket=tid, reason="heal_attempts_exhausted")
            continue
        healthy, backend, why = self_heal.rung_backend_healthy(t, tid)
        if not healthy:
            log.emit("skip", stage="release", ticket=tid, reason=f"rung_{why}", backend=backend)
            continue
        blocked_at = self_heal.parse_blocked_at(t.get("blocked_at")) or _utcnow()
        due.append((blocked_at, tid, backend, detail))
    due.sort(key=lambda d: (d[0], d[1]))
    released = 0
    for _, tid, backend, detail in due[:batch]:
        if dry_run:
            log.emit("would_release", ticket=tid, backend=backend, batch=batch)
            continue
        ok, out = self_heal.wq_release(tid)
        if not ok:
            log.emit("error", stage="release", ticket=tid, reason=f"wq release failed: {out}")
            continue
        released += 1
        log.emit("janitor_release", ticket=tid, backend=backend, batch=batch, detail=detail)
    held_back = max(0, len(due) - batch)
    if held_back:
        log.emit("info", stage="release", reason="batch_limit", batch=batch,
                 held_back=held_back, live_slots=batch // BATCH_SLOTS_FACTOR)
    return released, held_back


# ---- (b) auto-relane on an unambiguous title repo marker ---------------------------------

def unambiguous_lane(title, cfg):
    """The one enabled, non-human lane whose LANE_REPO is the repo the title's leading marker
    names; None when there is no marker or more (or fewer) than one such lane."""
    repo = router.title_repo_marker(title)
    if not repo:
        return None
    lanes = [lane for lane, path in router.LANE_REPO.items()
             if path == repo and (cfg["lanes"].get(lane) or {}).get("enabled")
             and lane not in fd.HUMAN_LANES]
    return lanes[0] if len(lanes) == 1 else None


def relane_marked(log, dry_run, cfg):
    relaned = 0
    for tid, row in sorted(gate_store.parked_rows().items()):
        if row["reason"] not in RELANE_REASONS:
            continue
        t = self_heal.wq_show(tid)
        if t.get("status") != "open" or secret_denied(t):
            continue
        lane = unambiguous_lane(t.get("title"), cfg)
        if not lane or lane == t.get("lane"):
            continue
        if dry_run:
            log.emit("would_relane", ticket=tid, frm=t.get("lane"), to=lane)
            continue
        p = subprocess.run([self_heal.WQ_BIN, "edit", str(tid), "--lane", lane],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            log.emit("error", stage="relane", ticket=tid, reason=(p.stdout + p.stderr).strip()[:300])
            continue
        gate_store.unpark(tid)
        relaned += 1
        log.emit("janitor_relane", ticket=tid, frm=t.get("lane"), to=lane, why=row["reason"])
    return relaned


# ---- (c) stale harness holds -------------------------------------------------------------

def clear_stale_harness_holds(log, dry_run):
    """Archive failure.json (-> failure.json.cleared) for an OPEN ticket whose HARNESS_DEFECT
    hold is provably stale. Never touches a blocked ticket's failure.json (its block reason
    cites the path) and never writes the dispatcher's state file (its witness entry drops on the
    dispatcher's next pass, when the failure.json is gone)."""
    seen = spawn_loop_guard.load_state().get("harness_defect_seen") or {}
    cleared = 0
    for tid in runaway.candidate_ticket_ids():
        fpath = os.path.join(self_heal.RUNS_ROOT, f"wq{tid}", "failure.json")
        try:
            with open(fpath) as f:
                rec = json.load(f)
            mtime_ns = os.stat(fpath).st_mtime_ns
        except (OSError, ValueError):
            continue
        if rec.get("triage") != "HARNESS_DEFECT":
            continue
        t = self_heal.wq_show(tid)
        if t.get("status") != "open" or secret_denied(t):
            continue
        ent = seen.get(str(tid)) if isinstance(seen, dict) else None
        edited = (isinstance(ent, dict) and ent.get("mtime_ns") == mtime_ns
                  and ent.get("fp") != runaway.edit_fingerprint(t))
        if not (edited or runaway.hold_released(rec, t)):
            continue
        why = "edited_since_failure" if edited else "paths_granted"
        if dry_run:
            log.emit("would_clear_hold", ticket=tid, why=why)
            continue
        try:
            os.replace(fpath, fpath + ".cleared")
        except OSError as e:
            log.emit("error", stage="harness_hold", ticket=tid, reason=repr(e)[:300])
            continue
        cleared += 1
        log.emit("janitor_clear_hold", ticket=tid, why=why)
    return cleared


# ---- (d) spawn-loop second-trip block ----------------------------------------------------

def block_spawn_loops(log, dry_run):
    if self_heal.charge_mode_legacy():
        return 0
    state = spawn_loop_guard.load_state()
    blocked = 0
    for tid in sorted(state.get(spawn_loop_guard.LEDGER_KEY) or {}):
        info = spawn_loop_guard.looping(state, tid)
        if not info or info.get("trips_24h", 0) < spawn_loop_guard.BLOCK_ON_TRIP:
            continue
        t = self_heal.wq_show(tid)
        if t.get("status") != "open" or secret_denied(t):
            continue
        reason = spawn_loop_guard.block_reason(t.get("notes"))
        if dry_run:
            log.emit("would_block", ticket=tid, reason=reason)
            continue
        if spawn_loop_guard.block_ticket(tid, reason, self_heal.WQ_BIN):
            blocked += 1
            log.emit("janitor_spawn_loop_block", ticket=tid, reason=reason,
                     trips_24h=info["trips_24h"])
        else:
            log.emit("error", stage="spawn_loop", ticket=tid, reason="block_failed")
    return blocked


# ---- (e) one digest row per cause per day ------------------------------------------------

def digest_body(cause, rows):
    lines = [f"wq#{r['ticket']} ({r['lane']}) {str(r.get('title') or '')[:70]}"
             for r in rows[:DIGEST_LIST_MAX]]
    if len(rows) > DIGEST_LIST_MAX:
        lines.append(f"... and {len(rows) - DIGEST_LIST_MAX} more")
    return (f"{len(rows)} open ticket(s) parked by the dispatcher gate `{cause}` — not re-evaluated "
            f"until edited or the recheck passes:\n" + "\n".join(lines))


def send_aq(cause, day, rows, aq_bin=None):
    """One aq row keyed per (cause, day). Fail-open: no aq binary / a failed call -> False and
    the caller does not mark the day as sent."""
    aq_bin = aq_bin or AQ_BIN
    if not os.path.exists(aq_bin):
        return False
    try:
        p = subprocess.run(
            [aq_bin, "add", AQ_SOURCE, f"parked-{cause}-{day}",
             f"dispatch: {len(rows)} ticket(s) parked — {cause}", digest_body(cause, rows)],
            capture_output=True, text=True, timeout=15)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def post_digests(log, dry_run, state, now=None, aq_bin=None):
    """Returns the causes a row was filed for this run (at most one each per UTC day)."""
    day = (now or _utcnow()).strftime("%Y-%m-%d")
    sent = []
    for cause, rows in sorted(gate_store.rows_by_reason().items()):
        if cause in SELF_RESOLVING or state["digest"].get(cause) == day:
            continue
        if dry_run:
            log.emit("would_digest", cause=cause, tickets=len(rows))
            continue
        if send_aq(cause, day, rows, aq_bin):
            state["digest"][cause] = day
            sent.append(cause)
            log.emit("janitor_digest", cause=cause, tickets=len(rows), day=day)
        else:
            log.emit("error", stage="digest", cause=cause, reason="aq_add_failed")
    return sent


# ---- the pass ---------------------------------------------------------------------------

def _section(log, name, fn, default):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 — a section's failure must not cost the others
        log.emit("error", stage=name, reason=f"section_failed: {e!r}"[:300])
        return default


def do_pass(log, dry_run):
    try:
        cfg, _warn, _sha = fd.load_config()
    except fd.ConfigError as e:
        log.emit("error", stage="config", reason=str(e))
        cfg = {"lanes": {}}
    state = load_state()
    purged = 0 if dry_run else _section(log, "purge", gate_store.purge_closed, 0)
    released, held_back = _section(log, "release", lambda: release_rung_faults(log, dry_run), (0, 0))
    relaned = _section(log, "relane", lambda: relane_marked(log, dry_run, cfg), 0)
    cleared = _section(log, "harness_hold", lambda: clear_stale_harness_holds(log, dry_run), 0)
    blocked = _section(log, "spawn_loop", lambda: block_spawn_loops(log, dry_run), 0)
    digests = _section(log, "digest", lambda: post_digests(log, dry_run, state), [])
    if digests and not dry_run:
        save_state(state)
    return {"purged": purged, "released": released, "held_back": held_back, "relaned": relaned,
            "holds_cleared": cleared, "spawn_loop_blocked": blocked, "digests": digests,
            "parked_by_reason": gate_store.by_reason()}


def run_pass(dry_run=False):
    pass_id = ("fjan-" + _utcnow().strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}")
    log = self_heal.DecisionLog(LOG_PATH, pass_id)
    ks = self_heal.kill_switch()
    if ks:
        log.emit("halt", stage="kill_switch", reason=ks)
        return 0
    holder = None
    if not dry_run:
        ok, holder = slock_acquire()
        if not ok:
            log.emit("halt", stage="lock", reason="lock_busy")
            return 0
    rc = 0
    try:
        log.emit("pass_start", dry_run=dry_run)
        log.emit("pass_end", **do_pass(log, dry_run))
    except Exception as e:  # noqa: BLE001 — pass boundary: log, never silent
        log.emit("error", stage="pass", reason=f"unhandled: {e!r}")
        rc = 1
    finally:
        if holder:
            slock_release(holder)
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="dispatch janitor (wq#2134)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one janitor pass and exit")
    r.add_argument("--once", action="store_true", help="documentational: a pass is one-shot")
    r.add_argument("--dry-run", action="store_true", help="decide + log would_*; zero side effects")
    a = ap.parse_args(argv)
    return run_pass(dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
