#!/usr/bin/env python3
"""capability_replay.py — replay real tickets through stage 3.7 without dispatching.

wq#1438 (BROKER_WIRING.md §4 row iv, live drill): "replay wq#1401/#1402/#1403/#1407
through the pre-skip in --dry-run: each lands in the column this doc predicts, zero
spawns." Those four are `blocked`, so a real `foreman_dispatcher --dry-run` pass skips
them at `not_open` long before stage 3.7 ever sees them — the drill needs a harness
that asks stage 3.7 the question directly.

That is all this does. It reads the queue READ-ONLY, runs the same
capability_lint.derive -> effective_needs -> preflight -> appliable_tags sequence
foreman_dispatcher.enforce_capability runs, and prints the column each ticket lands
in. It never writes a needs column, never spawns, and has no non-dry-run mode: the
dispatcher is the only writer (§2.1, "written by the DISPATCHER, never by the worker",
and there is no third writer either).

    python3 dispatcher/capability_replay.py 1401 1402 1403 1407
    python3 dispatcher/capability_replay.py --json 1401

Columns, in the dispatcher's own order:
    capability_denied_by_policy   lint refused a derived tag (deny-list or policy)
    needs_unresolved              lint saw a verb/node/path it could not pin to a tag
    cited_path_unreachable        preflight still refuses after the derived tags
    node_unknown                  ditto, for a node the fleet cannot address
    dispatchable                  clears the gate; `apply` names what would be written
                                  `+advisory` = it carries advisory findings (wq#2131):
                                  dispatched anyway, one needs_advisory note, no ntfy

`lint_clear` is the lint's verdict alone (no refused/unresolved), before preflight. An
advisory dir the worker cannot reach is lint_clear and, since wq#2184, dispatchable too:
preflight is handed the lint's `advisory_paths` and does not hold for a prose mention.
A needs:dir the ticket asked for, and every deny-listed path, still lands on a refusal.
"""
import argparse
import json
import os
import sqlite3
import sys

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(DISPATCHER_DIR, ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "orchestrator"))
sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import capability  # noqa: E402
import capability_lint  # noqa: E402
import router  # noqa: E402

QUEUE_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")
FIELDS = "id, lane, status, title, detail, needs, archetype"


def load_tickets(ticket_ids, db_path=None):
    con = sqlite3.connect(f"file:{db_path or QUEUE_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        qmarks = ",".join("?" for _ in ticket_ids)
        rows = con.execute(f"select {FIELDS} from tasks where id in ({qmarks})"
                           " order by id", tuple(ticket_ids)).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def replay(ticket, repo=None, roster=None, policy=None, sweep=None):
    """The verdict stage 3.7 would reach for this ticket, as a dict."""
    repo = repo if repo is not None else router.repo_for_ticket(ticket)
    derived = capability_lint.derive(
        ticket, repo=repo,
        add_dirs=capability_lint.base_add_dirs(ticket.get("archetype")),
        roster=roster, policy=policy, sweep=sweep)
    out = {"id": ticket["id"], "status": ticket.get("status"), "repo": repo,
           "mode": capability_lint.mode(), **derived.as_event(),
           # the lint's own verdict, before preflight (the real-reach check) is asked
           "lint_clear": not (derived.refused or derived.unresolved)}
    if derived.refused:
        return {**out, "column": capability.REASON_CAPABILITY_DENIED_BY_POLICY,
                "detail": "; ".join(derived.refused)}
    if derived.unresolved:
        return {**out, "column": capability.REASON_NEEDS_UNRESOLVED,
                "detail": "; ".join(derived.unresolved)}
    needs = capability.effective_needs(ticket, derived)
    add_dirs = capability.effective_add_dirs(ticket.get("archetype"), needs)
    verdict = capability.preflight(ticket, repo, add_dirs, needs,
                                   advisory_paths=derived.advisory_paths)
    if not verdict.ok:
        return {**out, "column": verdict.reason, "detail": verdict.summary}
    applied = capability_lint.appliable_tags(derived)
    return {**out, "column": "dispatchable",
            "apply": [f"{n}({a})" for n, a in applied],
            "detail": "clears the gate; no slot burned to find that out"}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="replay tickets through dispatcher stage 3.7 (read-only, no spawns)")
    ap.add_argument("tickets", nargs="+", type=int)
    ap.add_argument("--db", default=QUEUE_DB)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    tickets = load_tickets(a.tickets, a.db)
    missing = set(a.tickets) - {t["id"] for t in tickets}
    for tid in sorted(missing):
        print(f"wq#{tid}: no such ticket", file=sys.stderr)
    roster = capability_lint.load_roster()
    policy = capability_lint.load_policy()
    sweep = capability_lint.load_sweep()
    for t in tickets:
        r = replay(t, roster=roster, policy=policy, sweep=sweep)
        if a.json:
            print(json.dumps(r))
        else:
            extra = f" apply={r['apply']}" if r.get("apply") else ""
            if r["advisory"] and r["column"] == "dispatchable":
                extra += " +advisory"
            print(f"wq#{r['id']} [{r['status']}] -> {r['column']}{extra}\n"
                  f"    tags={r['tags']} lint_clear={r['lint_clear']} "
                  f"detail={r['detail'][:160]}")
            for a in r["advisory"]:
                print(f"    advisory: {a[:160]}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
