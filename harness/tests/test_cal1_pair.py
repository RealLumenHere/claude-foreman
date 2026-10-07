"""cal1_pair.py (wq#3618): attempt-record pickup and the results table."""
import json
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import cal1_pair as pair  # noqa: E402


def test_attempt_record_takes_latest_attempt(tmp_path):
    d = tmp_path / "wq7"
    d.mkdir()
    (d / "attempt-1.json").write_text(json.dumps({"turns": 1}))
    (d / "attempt-2.json").write_text(json.dumps({"turns": 2}))
    (d / "prior-attempt-notes.md").write_text("x")
    assert pair.attempt_record({"FOREMAN_RUNS_ROOT": str(tmp_path)}, 7) == {"turns": 2}
    assert pair.attempt_record({"FOREMAN_RUNS_ROOT": str(tmp_path)}, 8) == {}


def test_report_marks_failed_check(tmp_path, capsys):
    rows = [{"pair": "A", "cell": "claude-haiku-5-5", "ticket": 1, "wq_status": "done",
             "classification": "SUCCESS", "turns": 9, "elapsed_s": 40.0, "cost_usd": 0.05,
             "check_ok": True},
            {"pair": "A", "cell": "claude-sonnet-5@low", "ticket": 2, "wq_status": "claimed",
             "classification": None, "turns": None, "elapsed_s": 3.0, "cost_usd": None,
             "check_ok": False}]
    (tmp_path / "pair_results.json").write_text(json.dumps({"results": rows}))
    pair.report(str(tmp_path))
    out = capsys.readouterr().out.splitlines()
    assert out[2].endswith("| 0.050 | PASS |")
    assert out[3].endswith("| - | FAIL |")


def test_models_yaml_declares_every_trial_cell():
    # wq add refuses an undeclared model, which killed the first wq#3618 trial launch
    with open(pair.MODELS_YAML) as f:
        text = f.read()
    assert "id: claude-haiku-5-5" in text and "id: claude-sonnet-5" in text
