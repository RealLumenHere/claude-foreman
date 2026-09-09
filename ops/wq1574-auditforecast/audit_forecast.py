#!/usr/bin/env python3
"""audit_forecast.py — wq#1574: AuditForecast, a transparent forecasting scaffold.

arXiv:2609.05905 ("From Narrative to Auditable Forecasts: A Structured
Scaffold for Agentic Forecasting") argues that agentic forecasts should be
decomposable into a baseline (reference-class) probability, a retrieved
evidence set, and a *mechanical* sequence of odds updates — so a human can
replay every step instead of trusting a single opaque LLM verdict.

This module implements that scaffold:
  1. baseline_probability()   — anchor forecast in a stated base rate.
  2. gather_evidence()        — pull situational evidence via the fleet's
                                 SearXNG instance (127.0.0.1:8888, JSON API;
                                 see foreman/research/SEARXNG_FLEET_INTEGRATION.md).
  3. mechanical_odds_update() — fold evidence into the baseline in log-odds
                                 space, one auditable step per evidence item.
  4. build_report()           — assemble question + every intermediate value
                                 into a JSON report nothing here can fudge
                                 silently: every number in the final report
                                 traces back to a prior step's output.

Judging integration boundary (minerva_verify / minerva_factpack):
  The proposal asks this module to anchor baselines and gather context via
  minerva_factpack's retrieval and to have minerva_verify judge report
  quality. Both live at /home/lumen/minerva/*; their *contents* — including
  minerva_verify.py's actual CLI/import surface — are outside the foreman
  BUILD-worker sandbox's Read/Grep reach (ISSUE-LOG #233 / #240), so this
  module cannot verify a calling convention for them and must not guess one
  (house rule against fabricated integration points). `submit_for_judging()`
  below is therefore an honest no-op seam: it always reports
  "judge_unavailable" until a session with real read access to
  minerva_verify.py fills in the verified call. Do not add a subprocess
  call here without first reading that file — an invented flag will look
  like it "worked" (the path exists on disk) and then hang or fail
  silently against the real script.

Usage:
    audit_forecast.py "Will X happen by <date>?" --baseline 0.3 \\
        --evidence '[{"query": "X recent news", "stance": "support", "strength": 1.5}]'
    audit_forecast.py --self-test
"""
import argparse
import json
import math
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")

# Bound how much any single piece of evidence can move the odds. Mechanical
# updates are auditable, not unbounded — a "strength" of 100 should not let
# one search result swing a forecast to near-certainty.
MAX_LOG_LR = math.log(20.0)


def probability_to_log_odds(p):
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability must be in (0, 1), got {p}")
    return math.log(p / (1.0 - p))


def log_odds_to_probability(log_odds):
    odds = math.exp(log_odds)
    return odds / (1.0 + odds)


def baseline_probability(question, base_rate=None):
    """Anchor the forecast. Callers should pass an explicit reference-class
    base_rate (from minerva_factpack retrieval, a historical dataset, etc.);
    absent that, this scaffold refuses to invent one rather than silently
    defaulting to 0.5, which is a claim, not a neutral placeholder."""
    if base_rate is None:
        raise ValueError(
            "no base_rate supplied; AuditForecast does not guess a baseline "
            "for: " + question
        )
    if not 0.0 < base_rate < 1.0:
        raise ValueError(f"base_rate must be in (0, 1), got {base_rate}")
    return {
        "question": question,
        "base_rate": base_rate,
        "log_odds": probability_to_log_odds(base_rate),
    }


def gather_evidence(query, limit=5, timeout=10):
    """Query the fleet SearXNG instance for situational evidence. Returns a
    list of {title, url, snippet} dicts, or raises on total outage — callers
    decide whether a missing evidence source should abort or just be noted
    in the report (see build_report's evidence_error field)."""
    params = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{SEARXNG_URL}/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "AuditForecast/wq1574"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    results = data.get("results", [])[:limit]
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in results
    ]


def mechanical_odds_update(baseline, evidence_items):
    """Fold each evidence item into the baseline log-odds, one bounded,
    logged step at a time. evidence_items: list of dicts with
    {source, stance in {"support","refute"}, strength>0 (a likelihood-ratio
    magnitude the caller supplies and must justify in the report — this
    function does not infer stance/strength itself, that is the "mechanical,
    not vibes" property the source paper asks for)."""
    log_odds = baseline["log_odds"]
    steps = []
    for item in evidence_items:
        stance = item.get("stance")
        if stance not in ("support", "refute"):
            raise ValueError(f"evidence stance must be 'support' or 'refute', got {stance!r}")
        strength = float(item.get("strength", 1.0))
        if strength <= 0:
            raise ValueError(f"evidence strength must be > 0, got {strength}")
        signed_log_lr = math.log(strength)
        if stance == "refute":
            signed_log_lr = -signed_log_lr
        signed_log_lr = max(-MAX_LOG_LR, min(MAX_LOG_LR, signed_log_lr))
        log_odds_before = log_odds
        log_odds += signed_log_lr
        steps.append({
            "source": item.get("source", item.get("query", "unlabeled")),
            "stance": stance,
            "strength": strength,
            "log_lr_applied": signed_log_lr,
            "log_odds_before": log_odds_before,
            "log_odds_after": log_odds,
            "probability_after": log_odds_to_probability(log_odds),
        })
    return log_odds, steps


def submit_for_judging(report):
    """Judging integration seam for minerva_verify. Always a no-op today —
    see module docstring: minerva_verify.py's calling convention has never
    been verified from this sandbox, so this deliberately does not guess
    one. Wire a real subprocess/import call here only after reading that
    file's actual interface."""
    del report  # unused until a verified call replaces this stub
    return {"status": "judge_unavailable", "reason": "minerva_verify calling convention not yet verified"}


def build_report(question, base_rate, evidence_items, judge=False):
    baseline = baseline_probability(question, base_rate)
    final_log_odds, steps = mechanical_odds_update(baseline, evidence_items)
    report = {
        "question": question,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline": baseline,
        "steps": steps,
        "final_probability": log_odds_to_probability(final_log_odds),
        "final_log_odds": final_log_odds,
    }
    if judge:
        report["judging"] = submit_for_judging(report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--baseline", type=float, help="base rate in (0,1)")
    parser.add_argument("--evidence", default="[]", help="JSON list of evidence items")
    parser.add_argument("--judge", action="store_true", help="submit report to minerva_verify")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        report = build_report(
            "self-test: does mechanical_odds_update move probability toward support?",
            base_rate=0.2,
            evidence_items=[{"source": "self-test", "stance": "support", "strength": 3.0}],
        )
        assert report["final_probability"] > report["baseline"]["base_rate"]
        print("self-test OK")
        return 0

    if not args.question or args.baseline is None:
        parser.error("question and --baseline are required (or pass --self-test)")

    evidence_items = json.loads(args.evidence)
    report = build_report(args.question, args.baseline, evidence_items, judge=args.judge)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
