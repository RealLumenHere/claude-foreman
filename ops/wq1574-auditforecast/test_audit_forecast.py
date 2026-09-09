#!/usr/bin/env python3
"""Proof of wq#1574 AuditForecast's odds math and report scaffold.

Pure unit tests against the module's functions directly (no network, no
minerva reach required) plus one end-to-end run through the CLI as a real
subprocess (--self-test), matching this repo's ops/ test convention
(e.g. ops/wq1396-minerva-proposal-promoter/test_minerva_proposal_promoter.py).

    python3 ops/wq1574-auditforecast/test_audit_forecast.py

Exits 0 when every case passes.
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audit_forecast as af  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def test_probability_log_odds_roundtrip():
    for p in (0.01, 0.2, 0.5, 0.8, 0.99):
        log_odds = af.probability_to_log_odds(p)
        back = af.log_odds_to_probability(log_odds)
        check(f"roundtrip p={p}", math.isclose(back, p, rel_tol=1e-9), f"got {back}")


def test_probability_bounds_rejected():
    for bad in (0.0, 1.0, -0.1, 1.5):
        try:
            af.probability_to_log_odds(bad)
            check(f"rejects p={bad}", False, "did not raise")
        except ValueError:
            check(f"rejects p={bad}", True)


def test_baseline_requires_explicit_rate():
    try:
        af.baseline_probability("will it rain?")
        check("baseline requires base_rate", False, "did not raise")
    except ValueError:
        check("baseline requires base_rate", True)

    baseline = af.baseline_probability("will it rain?", base_rate=0.3)
    check("baseline carries base_rate", baseline["base_rate"] == 0.3)
    check(
        "baseline log_odds matches",
        math.isclose(af.log_odds_to_probability(baseline["log_odds"]), 0.3, rel_tol=1e-9),
    )


def test_mechanical_update_support_increases_probability():
    baseline = af.baseline_probability("q", base_rate=0.2)
    log_odds, steps = af.mechanical_odds_update(
        baseline, [{"source": "s1", "stance": "support", "strength": 3.0}]
    )
    p_final = af.log_odds_to_probability(log_odds)
    check("support raises probability", p_final > 0.2, f"got {p_final}")
    check("one step recorded", len(steps) == 1)
    check("step captures source", steps[0]["source"] == "s1")


def test_mechanical_update_refute_decreases_probability():
    baseline = af.baseline_probability("q", base_rate=0.8)
    log_odds, _steps = af.mechanical_odds_update(
        baseline, [{"source": "s1", "stance": "refute", "strength": 3.0}]
    )
    p_final = af.log_odds_to_probability(log_odds)
    check("refute lowers probability", p_final < 0.8, f"got {p_final}")


def test_mechanical_update_is_order_independent_in_log_space():
    baseline = af.baseline_probability("q", base_rate=0.5)
    items_a = [
        {"source": "s1", "stance": "support", "strength": 2.0},
        {"source": "s2", "stance": "refute", "strength": 4.0},
    ]
    items_b = list(reversed(items_a))
    log_odds_a, _ = af.mechanical_odds_update(baseline, items_a)
    log_odds_b, _ = af.mechanical_odds_update(baseline, items_b)
    check(
        "log-odds updates commute",
        math.isclose(log_odds_a, log_odds_b, rel_tol=1e-9),
        f"{log_odds_a} vs {log_odds_b}",
    )


def test_extreme_strength_is_bounded():
    baseline = af.baseline_probability("q", base_rate=0.5)
    log_odds, steps = af.mechanical_odds_update(
        baseline, [{"source": "s1", "stance": "support", "strength": 1e9}]
    )
    check(
        "log-LR clamped to MAX_LOG_LR",
        math.isclose(steps[0]["log_lr_applied"], af.MAX_LOG_LR, rel_tol=1e-9),
        f"got {steps[0]['log_lr_applied']}",
    )


def test_invalid_stance_rejected():
    baseline = af.baseline_probability("q", base_rate=0.5)
    try:
        af.mechanical_odds_update(baseline, [{"source": "s1", "stance": "maybe", "strength": 1.0}])
        check("invalid stance rejected", False, "did not raise")
    except ValueError:
        check("invalid stance rejected", True)


def test_build_report_without_judging():
    report = af.build_report(
        "will X happen?", 0.25, [{"source": "s1", "stance": "support", "strength": 2.0}]
    )
    check("report has question", report["question"] == "will X happen?")
    check("report has final_probability", 0.0 < report["final_probability"] < 1.0)
    check("report omits judging when not requested", "judging" not in report)


def test_submit_for_judging_degrades_without_minerva_reach():
    result = af.submit_for_judging({"question": "q"})
    check(
        "judging degrades gracefully when minerva_verify unreachable",
        result["status"] in ("judge_unavailable", "judge_error", "judged"),
        str(result),
    )


def test_cli_self_test_subprocess():
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "audit_forecast.py"), "--self-test"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    check("CLI --self-test exits 0", proc.returncode == 0, proc.stderr)
    check("CLI --self-test prints OK", "self-test OK" in proc.stdout, proc.stdout)


def main():
    test_probability_log_odds_roundtrip()
    test_probability_bounds_rejected()
    test_baseline_requires_explicit_rate()
    test_mechanical_update_support_increases_probability()
    test_mechanical_update_refute_decreases_probability()
    test_mechanical_update_is_order_independent_in_log_space()
    test_extreme_strength_is_bounded()
    test_invalid_stance_rejected()
    test_build_report_without_judging()
    test_submit_for_judging_degrades_without_minerva_reach()
    test_cli_self_test_subprocess()

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
        return 1
    print("\nall tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
