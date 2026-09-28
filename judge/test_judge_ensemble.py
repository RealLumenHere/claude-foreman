"""wq#2898: most-restrictive ensemble scoring over judge_bench_results rows."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge_ensemble as je

TS_A = "2026-09-28T03:47:54.9+00:00"
TS_B = "2026-09-28T06:14:47.5+00:00"


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE judge_bench_results(ts, model, case_id, expected_verdict, "
                 "actual_verdict, latency_s)")
    conn.executemany("INSERT INTO judge_bench_results VALUES (?,?,?,?,?,?)", rows)
    return conn


def test_combine_is_most_restrictive_and_fail_closed():
    assert je.combine(["allow", "allow-with-conditions"]) == "allow-with-conditions"
    assert je.combine(["allow", "deny"]) == "deny"
    assert je.combine(["allow", None]) == "deny"
    assert je.combine(["allow", "allow"]) == "allow"


def test_disjoint_false_allows_cancel_and_shared_one_survives():
    conn = _db([
        (TS_A, "a", "d1", "deny", "allow", 10.0),
        (TS_A, "a", "d2", "deny", "deny", 10.0),
        (TS_A, "a", "d3", "deny", "allow", 10.0),
        (TS_A, "a", "ok", "allow", "allow", 10.0),
        (TS_B, "b", "d1", "deny", "deny", 40.0),
        (TS_B, "b", "d2", "deny", "allow", 20.0),
        (TS_B, "b", "d3", "deny", "allow", 20.0),
    ])
    s = je.ensemble_stats([je.load_run(conn, "a@2026-09-28T03:47"),
                           je.load_run(conn, "b@2026-09-28T06")])
    assert s["false_allow_ids"] == ["d3"]
    assert (s["n"], s["n_allow"], s["allow_retained"]) == (3, 0, None)
    assert s["p95_latency_s"] == 20.0
    assert s["pct_over_30s"] == pytest.approx(0.333)


def test_ambiguous_ts_prefix_is_refused():
    conn = _db([(TS_A, "a", "d1", "deny", "deny", 1.0),
                (TS_B, "a", "d1", "deny", "deny", 1.0)])
    with pytest.raises(SystemExit):
        je.load_run(conn, "a@2026-09-28")
