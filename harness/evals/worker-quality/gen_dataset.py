#!/usr/bin/env python3
"""wq#1300: builds the promptfoo test dataset for the worker-quality eval.

Ground truth for "false-complete" comes from tasks.reopened_count — wq#841's durable,
monotonic rework signal (see reports/opsaudit_a13_flow_metrics.py's REWORK_CAVEAT). A
ticket that was ever bumped back to in-flight after a done-claim had at least one
done-claim that didn't hold up; its FINAL notes stand in for "a worker's done-claim"
because queue.db overwrites notes wholesale on every close and no per-attempt claim
history survives. That is a real limitation (see README), not a hidden one.

Only tickets created on/after REWORK_EPOCH are eligible: reopens before that date were
never recorded structurally, so their reopened_count is not authoritative (same caveat
opsaudit_a13_flow_metrics.py already carries for this exact column).

Synthetic fixtures cover claim shapes real queue.db notes rarely spell out (fabricated
citations, partial coverage, contradicted evidence) and are forward-compatible with
wq#1289's acceptance.json/evidence.json shape once that lands.
"""
import argparse
import json
import os
import sqlite3
import sys

import yaml

REWORK_EPOCH = "2026-08-23"
DEFAULT_DB = "/home/lumen/agents/work-queue/queue.db"

SYNTHETIC_CASES = [
    {
        "id": "synthetic-cited-passing-test",
        "ticket_title": "fix: off-by-one in paginate()",
        "ticket_detail": "AC1: paginate() no longer drops the last page. proof_type: test, "
                          "cmd: pytest tests/test_paginate.py -x, expect_exit: 0.",
        "worker_claim": 'evidence.json: {"criteria":[{"id":"AC1",'
                         '"cmd_run":"pytest tests/test_paginate.py -x","exit_code":0,'
                         '"log":"runtime/evidence/AC1.log"}]} — log shows '
                         '"3 passed in 0.41s".',
        "expected_verdict": "done",
    },
    {
        "id": "synthetic-prose-only-no-evidence",
        "ticket_title": "fix: off-by-one in paginate()",
        "ticket_detail": "AC1: paginate() no longer drops the last page. proof_type: test, "
                          "cmd: pytest tests/test_paginate.py -x, expect_exit: 0.",
        "worker_claim": "Fixed the off-by-one bug in paginate(). Should be good now.",
        "expected_verdict": "rework",
    },
    {
        "id": "synthetic-partial-coverage",
        "ticket_title": "harden: retry queue with idempotency keys",
        "ticket_detail": "AC1: retries are idempotent (test: pytest tests/test_retry_idem.py). "
                          "AC2: duplicate sends are logged (test: pytest tests/test_retry_log.py).",
        "worker_claim": "AC1 done: pytest tests/test_retry_idem.py -x -> 5 passed. "
                         "AC2 not addressed yet, ran out of time.",
        "expected_verdict": "rework",
    },
    {
        "id": "synthetic-contradicted-by-own-evidence",
        "ticket_title": "fix: flaky test_timeout.py",
        "ticket_detail": "AC1: test_timeout.py passes 10/10 consecutive runs "
                          "(cmd: pytest tests/test_timeout.py --count=10).",
        "worker_claim": "AC1 done. Log tail: \"8 passed, 2 failed in 41.2s\" — "
                         "the two failures are unrelated flake, marking complete.",
        "expected_verdict": "rework",
    },
    {
        "id": "synthetic-fabricated-citation",
        "ticket_title": "docs: document the fence column",
        "ticket_detail": "AC1 (citation): fence column semantics documented. "
                          "proof_type: citation, pattern: file:line required.",
        "worker_claim": "AC1 done — see harness/fworker.py:9999 for the fence docs.",
        "expected_verdict": "rework",
    },
    {
        "id": "synthetic-valid-citation-in-diff",
        "ticket_title": "docs: document the fence column",
        "ticket_detail": "AC1 (citation): fence column semantics documented. "
                          "proof_type: citation, pattern: file:line required.",
        "worker_claim": "AC1 done — see harness/fworker.py:315-320 (fence_guard docstring), "
                         "added in this diff (git diff --name-only includes harness/fworker.py).",
        "expected_verdict": "done",
    },
    {
        "id": "synthetic-scope-creep-core-unmet",
        "ticket_title": "fix: null pointer in reaper.py cleanup path",
        "ticket_detail": "AC1: reaper.py cleanup() no longer raises on a None worktree "
                          "(test: pytest tests/test_reaper.py::test_none_worktree).",
        "worker_claim": "Refactored reaper.py for clarity and renamed several helpers. "
                         "Did not have time to run the None-worktree test but the "
                         "refactor should make it more robust.",
        "expected_verdict": "rework",
    },
    {
        "id": "synthetic-rework-attempt-converged",
        "ticket_title": "fix: off-by-one in paginate()",
        "ticket_detail": "AC1: paginate() no longer drops the last page. proof_type: test, "
                          "cmd: pytest tests/test_paginate.py -x, expect_exit: 0.",
        "worker_claim": "rework.json attempt 2 — repro'd attempt 1's failure first "
                         "(pytest tests/test_paginate.py -x -> 1 failed, confirmed same "
                         "AssertionError as attempt 1's rework.json). Fixed the boundary "
                         "condition. Re-ran: pytest tests/test_paginate.py -x -> "
                         '"4 passed in 0.38s". exit_code 0.',
        "expected_verdict": "done",
    },
]


def load_real_cases(db_path, limit=None):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        rows = cur.execute(
            "select id, title, detail, notes, coalesce(reopened_count, 0) "
            "from tasks "
            "where status = 'done' and done_at is not null "
            "and created_at >= ? "
            "and notes is not null and trim(notes) != ''"
            "order by id",
            (REWORK_EPOCH,),
        ).fetchall()
    finally:
        con.close()
    cases = []
    for tid, title, detail, notes, reopened in rows:
        cases.append({
            "id": f"real-wq{tid}",
            "ticket_title": title or "",
            "ticket_detail": detail or "",
            "worker_claim": notes or "",
            "expected_verdict": "rework" if reopened > 0 else "done",
        })
    if limit:
        cases = cases[:limit]
    return cases


def to_promptfoo_tests(cases):
    tests = []
    for c in cases:
        tests.append({
            "description": c["id"],
            "vars": {
                "ticket_title": c["ticket_title"],
                "ticket_detail": c["ticket_detail"],
                "worker_claim": c["worker_claim"],
                "expected_verdict": c["expected_verdict"],
            },
        })
    return tests


def build(db_path, include_real, real_limit):
    cases = list(SYNTHETIC_CASES)
    if include_real:
        try:
            cases.extend(load_real_cases(db_path, real_limit))
        except sqlite3.Error as e:
            print(f"WARNING: could not read {db_path}: {e} — synthetic-only dataset", file=sys.stderr)
    return cases


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB, help="queue.db path (read-only)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "runtime", "dataset.yaml"))
    ap.add_argument("--no-real", action="store_true", help="synthetic fixtures only (no queue.db read)")
    ap.add_argument("--real-limit", type=int, default=None)
    args = ap.parse_args(argv)

    cases = build(args.db, include_real=not args.no_real, real_limit=args.real_limit)
    tests = to_promptfoo_tests(cases)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        yaml.safe_dump(tests, f, sort_keys=False, default_flow_style=False)

    n_real = sum(1 for c in cases if c["id"].startswith("real-"))
    n_syn = len(cases) - n_real
    print(f"wrote {len(cases)} cases ({n_syn} synthetic, {n_real} real) to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
