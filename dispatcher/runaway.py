#!/usr/bin/env python3
"""runaway.py — runaway-ticket detector (wq#718 AUTO-6).

A ticket that keeps getting claimed, attempted, and put back (released or blocked)
without ever reaching `done` will burn a fresh fworker attempt every dispatch cycle
forever unless something breaks the loop. self_heal.py (wq#725 AUTO-7) already treats
fworker's own attempt-N.json count (RUNS_ROOT/wq<id>/attempt-N.json, one file per
finished attempt) as the authoritative "how many times has this really been attempted"
signal — this module reuses that exact function (self_heal.attempt_count) rather than
keeping a second counter that could drift out of sync with the harness's real count.

`stuck_ticket_ids()` returns every ticket with >= RUNAWAY_MAX_ATTEMPTS finished attempts
whose current wq status is not `done` (a ticket that took a while but got there is not
a runaway). foreman_dispatcher.py's per-candidate loop skips anything in that set
(stage `runaway`) so a stuck ticket stops being handed to a fresh fworker every pass —
it stays visible in the queue (and on the dashboard) for a human to look at, it is
never silently dropped or auto-blocked.

CLI:  runaway.py list-stuck
"""
import argparse
import json
import os
import re
import sys

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import self_heal  # noqa: E402 — reuses attempt_count()/wq_show()/RUNS_ROOT

RUNAWAY_MAX_ATTEMPTS = int(os.environ.get("FOREMAN_RUNAWAY_MAX_ATTEMPTS", "5"))
RUN_DIR_RE = re.compile(r"^wq(\d+)$")


def candidate_ticket_ids():
    """Every ticket id that has at least one recorded attempt — the cheap first pass
    before the per-ticket wq_show() lookups in stuck_ticket_ids()."""
    try:
        entries = os.listdir(self_heal.RUNS_ROOT)
    except OSError:
        return []
    ids = []
    for name in entries:
        m = RUN_DIR_RE.match(name)
        if m:
            ids.append(int(m.group(1)))
    return ids


def stuck_ticket_ids(threshold=None):
    """{ticket_id: {"attempts", "status", "lane"}} for every non-done ticket at or
    above the attempt threshold."""
    threshold = threshold if threshold is not None else RUNAWAY_MAX_ATTEMPTS
    stuck = {}
    for tid in candidate_ticket_ids():
        n = self_heal.attempt_count(tid)
        if n < threshold:
            continue
        t = self_heal.wq_show(tid)
        if t.get("status") == "done":
            continue
        stuck[tid] = {"attempts": n, "status": t.get("status"), "lane": t.get("lane")}
    return stuck


def harness_defect_ticket_ids():
    """{ticket_id: {"summary", "status", "lane"}} for every open ticket whose most
    recent failure.json is still triaged HARNESS_DEFECT (wq#1415).

    fworker.py's handle_spawn_refused (wq#1331 SpawnRefused preflight, e.g.
    CITED_PATH_UNREACHABLE) writes failure.json but deliberately skips attempt-N.json
    ("attempt not charged" by design, so a transient refusal never eats into the real
    MAX_ATTEMPTS/circuit-breaker budget) and releases the ticket straight back to
    `open`. stuck_ticket_ids() above only ever sees attempt-N.json, so that loop never
    trips the runaway breaker either — it just gets re-claimed on the very next pass.
    Combined with fworker.py's trigger_refill() kicking a pass on every worker exit
    (up to every 2-4 min fleet-wide, far tighter than the 20-min timer this was sized
    for), the same unresolved defect got re-dispatched repeatedly. Hold here on the
    ticket's own last recorded triage instead of an attempt count; it clears itself as
    soon as a later attempt gets past preflight (a fresh non-HARNESS_DEFECT failure.json,
    or `done`/`blocked` takes the ticket out of `open`)."""
    held = {}
    for tid in candidate_ticket_ids():
        fpath = os.path.join(self_heal.RUNS_ROOT, f"wq{tid}", "failure.json")
        try:
            with open(fpath) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if rec.get("triage") != "HARNESS_DEFECT":
            continue
        t = self_heal.wq_show(tid)
        if t.get("status") != "open":
            continue
        held[tid] = {"summary": rec.get("summary"), "status": t.get("status"),
                     "lane": t.get("lane")}
    return held


def cmd_list_stuck(_args):
    print(json.dumps(stuck_ticket_ids(), indent=2))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="foreman runaway-ticket detector (wq#718)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-stuck", help="print stuck ticket ids + detail")
    a = ap.parse_args(argv)
    return {"list-stuck": cmd_list_stuck}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
