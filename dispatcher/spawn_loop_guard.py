#!/usr/bin/env python3
"""spawn_loop_guard.py — restart-loop breaker (wq#1703).

The two existing breakers both key on an artifact the WORKER writes after the fact:
runaway.py (wq#718) counts finished attempts (attempt-N.json), and
runaway.harness_defect_ticket_ids() (wq#1415) holds on the last recorded failure.json
triage. A ticket whose worker dies before writing either one is invisible to both.
ISSUE-LOG #283 is that hole measured: on 2026-09-10 05:00-07:00Z wq#1543 (a fleet-fires
probe) spawned 31 times and wq#1572 23 times in three hours — crash, claim released,
trigger_refill respawns instantly — with no attempt-N.json and no failure.json to show
for any of it, every spawn burning a tight-mode per-pass slot.

This module counts the one event that is always observable at the moment it happens:
the dispatcher's own spawn. `record_spawn()` appends {ts, attempts} to a per-ticket
ledger in dispatcher state (attempts = the finished-attempt count AT SPAWN TIME, the
baseline the next pass compares against), and `looping()` reports a ticket that reached
SPAWN_LOOP_MAX spawns inside SPAWN_LOOP_WINDOW_H without one attempt finishing. Progress
is deliberately the same authoritative signal runaway.py uses (self_heal.attempt_count)
rather than a second notion of "did it get anywhere" that could disagree with it.

The first trip only stops handing the ticket to a fresh worker, and `aq_row()` opens one
alert-queue row so the loop is visible to a human instead of silent. It clears itself: the
ledger window rolls forward, so the ticket becomes spawnable again once the burst ages
out, and any attempt that does finish lifts the guard immediately (and hands the ticket to
the wq#718 attempt-count breaker). DISPATCH_REBUILD step 8: the roll-off used to repeat
forever, so `looping()` now also counts trips per ticket over 24 h and the SECOND one blocks
the ticket with `SPAWN_LOOP: <last release note>` (FOREMAN_CHARGE_MODE=legacy restores the
roll-off only behaviour).

CLI:  spawn_loop_guard.py list-looping   # read live dispatcher state, print offenders
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import self_heal  # noqa: E402 — same attempt_count() runaway.py counts on

# Sized from ISSUE-LOG #283: the real loops ran 8-10 spawns/hour, so 3 spawns with
# nothing finished inside 3h is unambiguous, while an honest release-and-retry (a worker
# that finishes, writes attempt-N.json, and frees the ticket) never trips it at all.
SPAWN_LOOP_MAX = int(os.environ.get("FOREMAN_SPAWN_LOOP_MAX", "3"))
SPAWN_LOOP_WINDOW_H = float(os.environ.get("FOREMAN_SPAWN_LOOP_WINDOW_H", "3"))

LEDGER_KEY = "spawn_ledger"
TRIPS_KEY = "spawn_loop_trips"   # {tid: [iso ts]} one entry per loop episode
TRIPS_WINDOW_H = 24
BLOCK_ON_TRIP = 2                # the second trip inside TRIPS_WINDOW_H blocks the ticket
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq")
AQ_SOURCE = "foreman-dispatcher"
FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
STATE_PATH = os.environ.get("FOREMAN_DISPATCHER_STATE",  # same file the dispatcher writes
                            os.path.join(FOREMAN_ROOT, "dispatcher", "state.json"))


def _utcnow():
    return datetime.now(timezone.utc)


def _entry_ts(entry):
    try:
        return datetime.fromisoformat(entry["ts"])
    except (KeyError, TypeError, ValueError):
        return None


def _in_window(entry, now, window_h):
    ts = _entry_ts(entry)
    return ts is not None and now - ts < timedelta(hours=window_h)


def recent(ledger, tid, now=None, window_h=None):
    """This ticket's spawn entries still inside the window, oldest first."""
    now = now or _utcnow()
    window_h = SPAWN_LOOP_WINDOW_H if window_h is None else window_h
    entries = (ledger or {}).get(str(tid)) or []
    keep = [e for e in entries if isinstance(e, dict) and _in_window(e, now, window_h)]
    keep.sort(key=_entry_ts)
    return keep


def prune_ledger(ledger, now=None, window_h=None):
    """Drop every spawn older than the window (and every ticket left with none), so the
    ledger stays the size of one window's dispatch rate rather than growing forever.
    Called from load_state, the same place the ntfy dedup ledgers are pruned."""
    now = now or _utcnow()
    out = {}
    for tid in (ledger or {}):
        keep = recent(ledger, tid, now, window_h)
        if keep:
            out[str(tid)] = keep
    return out


def record_spawn(state, tid, now=None, attempts=None):
    """Append one spawn for `tid`. Call this right after a real spawn succeeds, before
    save_state — the entry is only meaningful if it is durable."""
    now = now or _utcnow()
    if attempts is None:
        attempts = self_heal.attempt_count(tid)
    ledger = state.setdefault(LEDGER_KEY, {})
    entries = ledger.setdefault(str(tid), [])
    entries.append({"ts": now.isoformat(timespec="seconds"), "attempts": attempts})
    return entries


def recent_trips(state, tid, now=None):
    """This ticket's recorded trips inside TRIPS_WINDOW_H, oldest first (datetimes)."""
    now = now or _utcnow()
    out = []
    for raw in (state.get(TRIPS_KEY) or {}).get(str(tid)) or []:
        try:
            ts = datetime.fromisoformat(raw)
            if now - ts < timedelta(hours=TRIPS_WINDOW_H):
                out.append(ts)
        except (TypeError, ValueError):  # unparseable, or naive (hand-edited state): not a trip
            continue
    return sorted(out)


def record_trip(state, tid, now=None):
    """Persist one trip (one loop episode). The caller saves state."""
    now = now or _utcnow()
    trips = state.setdefault(TRIPS_KEY, {}).setdefault(str(tid), [])
    trips.append(now.isoformat(timespec="seconds"))
    return trips


def prune_trips(trips, now=None):
    """Drop trips older than TRIPS_WINDOW_H (and tickets left with none); load_state calls it."""
    now = now or _utcnow()
    out = {}
    for tid in (trips or {}):
        keep = recent_trips({TRIPS_KEY: trips}, tid, now)
        if keep:
            out[str(tid)] = [ts.isoformat(timespec="seconds") for ts in keep]
    return out


def looping(state, tid, now=None, threshold=None, window_h=None):
    """None when the ticket is fine, else {"spawns", "since", "attempts", "trips_24h",
    "new_trip"}.

    Looping means: at least `threshold` spawns inside the window, and the finished-
    attempt count is no higher now than it was before the oldest of them. One finished
    attempt anywhere in the burst is progress — this guard is for the case where the
    dispatcher keeps paying for spawns that leave no trace at all.

    `trips_24h` counts loop EPISODES in the last 24 h, this one included (DISPATCH_REBUILD
    step 8): the guard used to roll off and let a looping ticket spawn 3 more, forever. An
    episode is recorded once, when the burst that tripped it starts after the previous trip;
    `new_trip` says this one is not recorded yet (record_trip, then save state)."""
    threshold = SPAWN_LOOP_MAX if threshold is None else threshold
    entries = recent(state.get(LEDGER_KEY), tid, now, window_h)
    if len(entries) < threshold:
        return None
    baseline = min(e.get("attempts") or 0 for e in entries)
    attempts = self_heal.attempt_count(tid)
    if attempts > baseline:
        return None
    trips = recent_trips(state, tid, now)
    new_trip = not trips or _entry_ts(entries[0]) > trips[-1]
    return {"spawns": len(entries), "since": entries[0]["ts"], "attempts": attempts,
            "trips_24h": len(trips) + (1 if new_trip else 0), "new_trip": new_trip}


def release_note(notes):
    """The last release note on a ticket, else its last note line: what SPAWN_LOOP blocks quote."""
    lines = [ln.strip() for ln in (notes or "").splitlines() if ln.strip() and ln.strip() != "None"]
    released = [ln for ln in lines if "releas" in ln.lower()]
    return ((released or lines or ["no release note"])[-1])[:300]


def block_reason(notes):
    return f"SPAWN_LOOP: {release_note(notes)}"


def block_ticket(tid, reason, wq_bin=None):
    """`wq block` an open ticket (no claim to fence: owner_ok passes when claimed_by is empty).
    Fail-open: False on any failure, the skip already happened."""
    try:
        p = subprocess.run([wq_bin or WQ_BIN, "block", str(tid), reason],
                           capture_output=True, text=True, timeout=30)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def aq_message(tid, info, window_h=None):
    window_h = SPAWN_LOOP_WINDOW_H if window_h is None else window_h
    return (f"wq#{tid} was spawned {info['spawns']}x since {info['since']} "
            f"(window {window_h}h) and not one attempt finished "
            f"(attempt-N.json count still {info['attempts']}). The dispatcher has "
            f"stopped re-spawning it; it stays open and skips every pass until the "
            f"burst ages out of the window or an attempt completes. Look for a worker "
            f"that dies before writing attempt-N.json (ISSUE-LOG #283 class).")


def aq_row(tid, info, aq_bin=None, window_h=None):
    """Open one alert-queue row for a looping ticket. aq's own (source,key) dedup bumps
    an existing OPEN row instead of duplicating it, so re-calling is safe. Fail-open:
    no aq binary, or a failed call, must never break the dispatch pass — the skip has
    already happened by the time we get here."""
    aq_bin = aq_bin or AQ_BIN
    if not os.path.exists(aq_bin):
        return False
    try:
        p = subprocess.run(
            [aq_bin, "add", AQ_SOURCE, f"spawn-loop-wq{tid}",
             f"wq#{tid}: spawn loop — {info['spawns']} spawns, no finished attempt",
             aq_message(tid, info, window_h)],
            capture_output=True, text=True, timeout=15)
        return p.returncode == 0
    except Exception:  # noqa: BLE001 — see docstring
        return False


def load_state(path=None):
    try:
        with open(path or STATE_PATH) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return {}
    return st if isinstance(st, dict) else {}


def cmd_list_looping(args):
    state = load_state(args.state)
    out = {}
    for tid in (state.get(LEDGER_KEY) or {}):
        info = looping(state, tid)
        if info:
            out[str(tid)] = info
    print(json.dumps(out, indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="foreman spawn-loop guard (wq#1703)")
    ap.add_argument("--state", help=f"dispatcher state file (default {STATE_PATH})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-looping", help="print ticket ids the guard would refuse to spawn")
    a = ap.parse_args(argv)
    return {"list-looping": cmd_list_looping}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
