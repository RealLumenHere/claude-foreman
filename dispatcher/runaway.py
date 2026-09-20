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
import time

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import self_heal  # noqa: E402 — reuses attempt_count()/wq_show()/RUNS_ROOT
import capability  # noqa: E402 — wq#1438 §2.2 hold release
import capability_lint  # noqa: E402

RUNAWAY_MAX_ATTEMPTS = int(os.environ.get("FOREMAN_RUNAWAY_MAX_ATTEMPTS", "5"))
RUNAWAY_WINDOW_DAYS = int(os.environ.get("FOREMAN_RUNAWAY_WINDOW_DAYS", "7"))
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


def _pinned(row, col):
    """A ticket's pinned model/archetype; `wq show` prints a NULL column as "None"."""
    v = (row.get(col) or "").strip()
    return "" if v == "None" else v


def _attempt_time(path, rec):
    for key in ("finished_at", "started_at"):
        v = rec.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _predates_edit(rec, model, archetype):
    """True when the attempt ran under a different model/archetype than the ticket has
    now. Only a pinned column can differ, and a record that never noted its model or
    archetype (older shape) is never called stale."""
    if model and rec.get("model") and model not in (rec.get("model"), rec.get("observed_model")):
        return True
    return bool(archetype and rec.get("archetype") and rec["archetype"] != archetype)


def charged_attempts(tid, row, db_attempts, now=None):
    """The attempt count the runaway breaker compares to its threshold.

    Counts attempt files charged to the ticket (charged_to absent = ticket, so history
    written before fworker stamped the field still counts), finished inside
    RUNAWAY_WINDOW_DAYS, and newer than the last attempt that ran under a different
    model or archetype than the ticket's current pin — wq keeps no edit timestamp, so
    the attempt records are the only witness to an edit. A rung/harness/budget fault, an
    attempt from before an edit, and an old attempt are not evidence the ticket is
    unworkable.

    The wq `attempt` column still catches claims that never wrote a file (wq#1671): its
    surplus over the total file count is added on top. With every file ticket-charged and
    in window this reduces to max(files, column), the pre-step-5 rule."""
    now = now if now is not None else time.time()
    horizon = now - RUNAWAY_WINDOW_DAYS * 86400
    model, archetype = _pinned(row, "model"), _pinned(row, "archetype")
    recs = [(_attempt_time(p, r), r) for p, r in self_heal.attempt_records(tid)]
    edit_at = max((at for at, r in recs if _predates_edit(r, model, archetype)), default=0.0)
    counted = sum(1 for at, r in recs
                  if self_heal.charged_to(r) == "ticket" and at >= horizon and at > edit_at)
    return counted + max(0, db_attempts - len(recs))


def stuck_ticket_ids(threshold=None):
    """{ticket_id: {"attempts", "status", "lane"}} for every non-done ticket at or
    above the attempt threshold.

    wq#1671: attempt_count() only sees attempt-N.json files, which fworker writes at
    write_attempt_state() well after wq_claim() already succeeded. A claim that then
    dies before that point -- an unhandled exception in do_attempt() (wq#649's
    make_worktree git-plumbing repro is the known case), or a hard external kill
    (OOM/SIGKILL) that no Python except block can catch -- leaves n at 0 forever while
    the ticket keeps getting reclaimed every pass. The wq row's own `attempt` column
    (bin/wq: COALESCE(attempt,0)+1, written atomically IN the claiming UPDATE, wq#582)
    has no such gap: it climbs on every successful claim regardless of what the
    claimed process manages to write afterward, or whether it gets to run at all.
    Comparing the wq column is not a replacement for the file count (a ticket can be
    claimed once and legitimately spend N real attempts before any of them finishes,
    which the file count still tracks correctly) -- it is a second, independent signal
    that catches the specific class of loop the file count is structurally blind to.
    Live evidence this is not theoretical: wq#1961 sits at file-count 1 / wq-column 10
    as of 2026-09-18, 9 claims that produced no attempt-N.json.

    effective_attempts is the max of the two counters, not their sum: a resumed
    session's later attempts share one claim (one wq-column bump for possibly several
    attempt-N.json files, e.g. handle_resumable_failure's retry ladder), so summing
    would double count and could false-positive a ticket that is legitimately still
    within budget.

    DISPATCH_REBUILD_SPEC step 5: the file side of that max is charged_attempts() — only
    ticket-charged attempts inside the window and after the last model/archetype edit —
    unless FOREMAN_CHARGE_MODE=legacy, which restores the plain file count.

    wq#2082: the wq column for every candidate comes from ONE batched read
    (self_heal.wq_rows), not a `wq show` subprocess per RUNS_ROOT dir — that per-dir
    subprocess (wq#1671, 2026-09-18) grew with the run-dir count to 171s of a 197s pass
    (research/DISPATCH_STALL_RCA.md §3.4)."""
    threshold = threshold if threshold is not None else RUNAWAY_MAX_ATTEMPTS
    stuck = {}
    ids = candidate_ticket_ids()
    rows = self_heal.wq_rows(ids)
    for tid in ids:
        t = rows.get(tid) or {}
        try:
            db_attempts = int(t.get("attempt") or 0)
        except (TypeError, ValueError):
            db_attempts = 0
        if self_heal.charge_mode_legacy():
            effective = max(self_heal.attempt_count(tid), db_attempts)
        else:
            effective = charged_attempts(tid, t, db_attempts)
        if effective < threshold:
            continue
        if t.get("status") == "done":
            continue
        stuck[tid] = {"attempts": effective, "status": t.get("status"), "lane": t.get("lane")}
    return stuck


def hold_released(rec, ticket):
    """wq#1438 (BROKER_WIRING.md §2.2, last para): True when a HARNESS_DEFECT hold is
    stale because the thing it held for has since been granted.

    The only defect the pre-skip can retire this way is CITED_PATH_UNREACHABLE, and
    only through its STRUCTURED field: `unreachable_paths` (schema v2, wq#1436 §2.5 —
    v1 records always wrote it empty, which is why covered_by() treats empty as
    "tells us nothing" rather than "nothing left to cover"). If every path that
    refusal named is now inside the ticket's effective add_dirs — its own needs:dir
    tags plus whatever stage 3.7 derives for it this pass — then the next spawn will
    get past preflight and holding it is just a loop that never ends. Without this,
    auto-tagging a held ticket could never let it run (§2.2).

    Anything else (WORKTREE_REPO_MISMATCH, a hook crash, a v1 record) keeps its hold:
    the backstop is only bypassed for a defect this module can prove is fixed."""
    paths = (rec or {}).get("unreachable_paths") or []
    if not paths:
        return False
    try:
        derived = capability_lint.derive(ticket) if capability_lint.enabled() else None
        needs = capability.effective_needs(ticket, derived)
        add_dirs = capability.effective_add_dirs(ticket.get("archetype"), needs)
    except Exception:  # noqa: BLE001 — a lint failure must never RELEASE a hold
        return False
    return capability.covered_by(paths, add_dirs)


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
    or `done`/`blocked` takes the ticket out of `open`) — or, since wq#1438, as soon as
    hold_released() can prove the paths the refusal named are now granted."""
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
        if hold_released(rec, t):  # wq#1438 §2.2: the cited paths are granted now
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
