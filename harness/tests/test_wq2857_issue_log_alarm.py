"""wq#2857: regression cover for the pinned-baseline alarm around wq#2817's
ISSUE-LOG pointer checker.

probe_pointers.py (wq#2817) reports every DUP/DANGLING/ONE-WAY finding in
kb/ISSUE-LOG.md, but the live log already carries a 128-row backlog (wq#2817
baselined 124, two more collisions landed since) that isn't being renumbered
in place (see reports/2857_policy_issue_log_row_disambiguation.md) -- so a
periodic run of probe_pointers.py alone would either fail forever or need
constant manual re-baselining. alarm_new_findings.py (wq#2857) instead diffs
the live finding set against a pinned baseline and alarms only on a finding
the baseline doesn't already know about, i.e. a fresh collision.

These tests run against synthetic fixture tables and an in-memory baseline,
never the live kb file or the committed baseline snapshot, so they stay
re-runnable after either one moves on.
"""
import importlib.util
import json
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)  # conftest's autouse fixture imports fworker
REPO = os.path.dirname(HARNESS_DIR)
OPS_DIR = os.path.join(REPO, "ops", "wq2817-issue-log-pointer-integrity")

_probe_spec = importlib.util.spec_from_file_location(
    "probe_pointers", os.path.join(OPS_DIR, "probe_pointers.py")
)
probe = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(probe)

_alarm_spec = importlib.util.spec_from_file_location(
    "alarm_new_findings", os.path.join(OPS_DIR, "alarm_new_findings.py")
)
alarm = importlib.util.module_from_spec(_alarm_spec)
_alarm_spec.loader.exec_module(alarm)

FIXTURE_TWO_DUPS = [
    "| 836 | wq#1789 pilot | calibration collapsed | retried | OPEN |",
    "| 836 | wq#2740 same run | five tests fail on main | filed | OPEN |",
    "| 869 | wq#2787 finding | cause a | fix a | OPEN |",
    "| 869 | wq#2836 finding | cause b | fix b | OPEN |",
]


def keys_for(lines):
    return probe.finding_keys(probe.rows(lines))


def write_baseline(path, keys):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"captured": "2026-01-01T00:00:00Z", "count": len(keys), "keys": sorted(keys)}, fh)


def test_baseline_matching_live_findings_is_not_a_new_finding(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    write_baseline(baseline_path, keys_for(FIXTURE_TWO_DUPS))

    baseline_keys, _ = alarm.load_baseline(str(baseline_path))
    live_keys = keys_for(FIXTURE_TWO_DUPS)
    assert live_keys - baseline_keys == set()


def test_a_fresh_collision_beyond_the_baseline_is_a_new_finding(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    write_baseline(baseline_path, keys_for(FIXTURE_TWO_DUPS))
    grown = FIXTURE_TWO_DUPS + [
        "| 900 | wq#3001 finding | cause c | fix c | OPEN |",
        "| 900 | wq#3002 collides with 3001 | cause d | fix d | OPEN |",
    ]
    live_path = tmp_path / "ISSUE-LOG.md"
    live_path.write_text("\n".join(grown) + "\n")

    baseline_keys, _ = alarm.load_baseline(str(baseline_path))
    found = probe.rows(probe.load(None, str(live_path)))
    live_keys = keys_for(grown)
    new_keys = live_keys - baseline_keys
    assert new_keys == {"DUP:900:2"}


def test_a_baseline_finding_that_was_cleaned_up_is_resolved_not_alarmed(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    write_baseline(baseline_path, keys_for(FIXTURE_TWO_DUPS))
    # row 836's second copy renumbered away -- only the 869 collision remains.
    cleaned = FIXTURE_TWO_DUPS[:1] + FIXTURE_TWO_DUPS[2:]
    live_path = tmp_path / "ISSUE-LOG.md"
    live_path.write_text("\n".join(cleaned) + "\n")

    baseline_keys, _ = alarm.load_baseline(str(baseline_path))
    live_keys = keys_for(cleaned)
    assert live_keys - baseline_keys == set()
    assert baseline_keys - live_keys == {"DUP:836:2"}


def test_finding_keys_are_stable_across_unrelated_line_shifts():
    padding = [f"| {n} | padding | padding | padding | OPEN |" for n in range(1, 6)]
    assert keys_for(padding + FIXTURE_TWO_DUPS) == keys_for(FIXTURE_TWO_DUPS)


def test_committed_baseline_snapshot_is_well_formed():
    with open(os.path.join(OPS_DIR, "known-findings-baseline.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["count"] == len(data["keys"]) == len(set(data["keys"]))
    assert all(k.split(":")[0] in ("DUP", "DANGLING", "ONEWAY") for k in data["keys"])


def test_missing_baseline_file_is_reported_not_raised(tmp_path, capsys):
    old_argv = sys.argv
    sys.argv = ["alarm_new_findings.py", "--baseline", str(tmp_path / "missing.json")]
    try:
        rc = alarm.main()
    finally:
        sys.argv = old_argv
    assert rc == 1
    assert "cannot read baseline" in capsys.readouterr().err
