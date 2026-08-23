#!/usr/bin/env python3
"""wq#839 regression test: every claim fworker_stats.py prints must be recomputed.

The bug this guards against (wq#839, escalated from wq#776's verify-pass of
wq#751/PR#46): §3 and §6 carried prose baked from the original 69-ticket run, so
a rerun printed "zero measured successes" for an archetype whose freshly-rendered
table two lines above showed 8 successes.

A literal-number check is not enough — refreshing the literals would pass it and
reintroduce the bug on the next rerun. So each test builds a synthetic dataset
whose correct answer is the OPPOSITE of the live one and asserts the narrative
followed the data.

Run: python3 -m pytest ops/wq751-worker-success/test_fworker_stats.py
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SRC = Path(__file__).with_name("fworker_stats.py")
_spec = importlib.util.spec_from_file_location("fworker_stats", _SRC)
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)


def attempt(ticket, n=1, cls="SUCCESS", turns=10, max_turns=100, arch="standard-engineering",
            model="claude-opus-5", **kw):
    a = {
        "ticket": ticket, "attempt": n, "classification": cls, "turns": turns,
        "max_turns": max_turns, "archetype": arch, "model": model, "elapsed_s": 60.0,
        "session_id": f"sess-{ticket}-{n}", "oauth_token_used": True,
        "killed_reason": None, "finished_at": 1787000000.0 + ticket * 100 + n,
        "total_cost_usd": 0.0,
    }
    a.update(kw)
    return a


def build(tmp_path, attempts, tickets):
    runs = tmp_path / "_runs"
    for a in attempts:
        d = runs / f"wq{a['ticket']}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"attempt-{a['attempt']}.json").write_text(json.dumps(a))
    db = tmp_path / "queue.db"
    con = sqlite3.connect(db)
    con.execute("create table tasks (id integer primary key, detail text, lane text, gate text)")
    con.executemany("insert into tasks values (?,?,?,?)", tickets)
    con.commit()
    con.close()
    return str(runs / "wq*" / "attempt-*.json"), str(db)


def render(tmp_path, attempts, tickets):
    glob_pat, db = build(tmp_path, attempts, tickets)
    loaded, unreadable = fs.load_attempts(glob_pat)
    assert not unreadable
    assert len(loaded) == len(attempts), "fixture did not round-trip through load_attempts"
    meta = fs.load_tickets(db, sorted({a["ticket"] for a in loaded}))
    return {
        "s2": fs.section_turns_distribution(loaded),
        "s3": fs.section_false_complete(loaded, meta),
        "s4": fs.section_retries(loaded),
        "s6": fs.section_recommendation(loaded),
    }


def test_gate_line_counts_tickets_in_this_dataset(tmp_path):
    """The original printed a literal "69 tickets" forever. Give it 3."""
    attempts = [attempt(t) for t in (1, 2, 3)]
    tickets = [(t, "detail here", "build", "") for t in (1, 2, 3)]
    out = render(tmp_path, attempts, tickets)["s3"]
    assert "empty string for 100% of the 3 tickets" in out
    assert "69 tickets" not in out


def test_gate_line_reports_coverage_once_gates_are_populated(tmp_path):
    """The claim "wq's gate mechanism is unused" must not survive gates existing."""
    attempts = [attempt(t) for t in range(1, 5)]
    tickets = [(t, "detail here", "build", "tests-pass" if t <= 2 else "") for t in range(1, 5)]
    out = render(tmp_path, attempts, tickets)["s3"]
    assert "populated on 2/4 (50%)" in out
    assert "unused, not protective" not in out


def test_no_zero_success_callout_when_every_archetype_succeeded(tmp_path):
    """Live repro: table showed verify-pass 8 successes, prose said zero."""
    attempts = [attempt(1, arch="verify-pass", turns=97),
                attempt(2, arch="standard-engineering", turns=50)]
    tickets = [(t, "detail", "build", "") for t in (1, 2)]
    out = render(tmp_path, attempts, tickets)["s6"]
    assert "| verify-pass | 1 |" in out
    assert "zero measured successes" not in out
    assert "at least one measured SUCCESS" in out


def test_zero_success_callout_names_only_archetypes_actually_absent(tmp_path):
    attempts = [attempt(t, arch="verify-pass", cls="KILLED") for t in range(1, 7)]
    attempts += [attempt(9, arch="standard-engineering")]
    tickets = [(t, "detail", "build", "") for t in list(range(1, 7)) + [9]]
    out = render(tmp_path, attempts, tickets)["s6"]
    assert "`verify-pass` (6 attempts) — zero measured successes" in out
    assert "standard-engineering" not in out.split("### Falsifiable")[0].split("|")[-1]


def test_thin_zero_success_archetype_is_not_called_a_bigger_problem(tmp_path):
    attempts = [attempt(1, arch="research", cls="KILLED"), attempt(2)]
    tickets = [(t, "detail", "build", "") for t in (1, 2)]
    out = render(tmp_path, attempts, tickets)["s6"]
    assert "too few attempts" in out
    assert "bigger problem than any ceiling number" not in out


def test_predictions_are_renumbered_and_carry_computed_baselines(tmp_path):
    """Original hardcoded 16/79, 3/62, 0/12 and max_turns 25-120."""
    attempts = [attempt(1, cls="KILLED", max_turns=200),
                attempt(1, n=2, cls="SUCCESS", turns=150, max_turns=200),
                attempt(2, cls="FALSE_COMPLETE"),
                attempt(3)]
    tickets = [(t, "detail", "build", "") for t in (1, 2, 3)]
    out = render(tmp_path, attempts, tickets)["s6"]
    for stale in ("16/79", "3/62", "0/12", "25-120"):
        assert stale not in out
    assert "1/4 (25%) attempt-level rate" in out
    assert "1/1 (100%) same-ceiling baseline" in out


def test_prediction_1_rationale_tracks_section_3_mechanism(tmp_path):
    """§6 must not cite KILLED-then-FALSE_COMPLETE as the cause when §3 measured
    it as a minority — that is the same cross-section contradiction as wq#839."""
    attempts = [attempt(t, cls="FALSE_COMPLETE") for t in range(1, 6)]
    tickets = [(t, "detail", "build", "") for t in range(1, 6)]
    out = render(tmp_path, attempts, tickets)
    assert "0/5 (0%) had a KILLED attempt first" in out["s3"]
    assert "only weakly" in out["s6"]


def test_kill_offset_claim_follows_the_data(tmp_path):
    """"killed at exactly max_turns+5, never earlier, never later" was asserted."""
    attempts = [attempt(1, cls="KILLED", killed_reason="turns_exceeded",
                        max_turns=100, turns=137),
                attempt(2, cls="KILLED", killed_reason="turns_exceeded",
                        max_turns=100, turns=105),
                attempt(3)]
    tickets = [(t, "detail", "build", "") for t in (1, 2, 3)]
    out = render(tmp_path, attempts, tickets)["s2"]
    assert "never earlier, never later" not in out
    assert "spanned +5 to +37" in out


def test_later_successes_under_identical_ceiling_are_not_called_escalations(tmp_path):
    """"every one of those later successes came from a DIFFERENT max_turns" was asserted."""
    attempts = [attempt(1, cls="KILLED", max_turns=100),
                attempt(1, n=2, cls="SUCCESS", turns=90, max_turns=100)]
    tickets = [(1, "detail", "build", "")]
    out = render(tmp_path, attempts, tickets)["s4"]
    assert "0/1 (0%) of those later successes ran under a DIFFERENT max_turns" in out
    assert "raising max_turns before the retry" not in out


def test_short_spec_correlation_is_dropped_when_it_does_not_hold(tmp_path):
    attempts = [attempt(1, cls="FALSE_COMPLETE"), attempt(2, cls="SUCCESS")]
    tickets = [(1, "x" * 5000, "build", ""), (2, "short", "build", "")]
    out = render(tmp_path, attempts, tickets)["s3"]
    assert "is NOT the shortest bucket" in out
    assert "vague/short specs correlate" not in out


def test_lane_narrative_names_the_lanes_actually_elevated(tmp_path):
    """Original hardcoded "auto and mission-control ... n=3 each"."""
    attempts = [attempt(t, cls="FALSE_COMPLETE") for t in (1, 2)]
    attempts += [attempt(t) for t in (3, 4, 5)]
    tickets = [(1, "d", "memory", ""), (2, "d", "memory", "")]
    tickets += [(t, "d", "build", "") for t in (3, 4, 5)]
    out = render(tmp_path, attempts, tickets)["s3"]
    assert "`memory` 2/2 (100%)" in out
    assert "mission-control" not in out
    assert "n=3 each" not in out


def test_unreadable_attempt_files_are_reported_not_swallowed(tmp_path):
    """A dropped attempt file silently shrinks every denominator — the wq#839
    failure mode (numbers that no longer describe the data) in another coat."""
    glob_pat, _ = build(tmp_path, [attempt(1), attempt(2)],
                        [(t, "d", "build", "") for t in (1, 2)])
    (tmp_path / "_runs" / "wq3").mkdir(parents=True)
    (tmp_path / "_runs" / "wq3" / "attempt-1.json").write_text("{truncated")

    loaded, unreadable = fs.load_attempts(glob_pat)
    assert len(loaded) == 2
    assert len(unreadable) == 1


def test_section_6_prose_never_contradicts_its_own_table(tmp_path):
    """The exact shape of wq#839: §6 named an archetype as having zero measured
    successes while the table it sits under credited that archetype with 8.

    Rendered against a dataset deliberately unlike the original — the archetypes
    that failed there succeed here, and vice versa."""
    flipped = [
        attempt(1, arch="verify-pass", turns=97),
        attempt(2, arch="research", turns=30, max_turns=40),
        attempt(3, arch="mechanical-edit", cls="KILLED", killed_reason="turns_exceeded",
                turns=105, max_turns=100),
        attempt(4, arch="mechanical-edit", cls="FALSE_COMPLETE"),
    ]
    tickets = [(t, "detail", "build", "") for t in range(1, 5)]
    out = render(tmp_path, flipped, tickets)["s6"]

    credited = {line.split("|")[1].strip(): int(line.split("|")[2].strip())
                for line in out.splitlines()
                if line.startswith("|") and line.split("|")[2].strip().isdigit()}
    assert credited == {"verify-pass": 1, "research": 1, "mechanical-edit": 0}

    for arch, n_success in credited.items():
        if n_success:
            assert f"`{arch}` ({n_success}" not in out, (
                f"§6 prose calls `{arch}` zero-success while its own table row "
                f"credits it with {n_success}")
    assert "`mechanical-edit` (2 attempts) — zero measured successes" in out


@pytest.mark.parametrize("stale", [
    "69 tickets", "n=3 each", "16/79", "3/62", "0/12", "25-120",
    "Current state (2026-08-22)", "lifted to 600",
])
def test_no_literals_from_the_original_run_survive_in_source(stale):
    assert stale not in _SRC.read_text(), (
        f"{stale!r} is a literal from the original 2026-08-22 run (wq#839); "
        f"derive it from the loaded attempts instead")
