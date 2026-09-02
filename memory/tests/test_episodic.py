"""episodic.py unit tests (wq#1183) — append-only contract, per-run layout,
malformed-line tolerance. Zero quota, no fleet paths."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import episodic  # noqa: E402


def test_append_creates_per_run_file(tmp_path):
    path = episodic.append_event(42, {"type": "attempt_end", "attempt": 1},
                                 runs_root=str(tmp_path))
    assert path == str(tmp_path / "wq42" / "episode.jsonl")
    assert os.path.isfile(path)


def test_append_is_append_only_and_stamps_ts(tmp_path):
    episodic.append_event(7, {"type": "a", "attempt": 1}, runs_root=str(tmp_path))
    episodic.append_event(7, {"type": "b", "attempt": 2}, runs_root=str(tmp_path))
    events = episodic.read_events(7, runs_root=str(tmp_path))
    assert [e["type"] for e in events] == ["a", "b"]
    assert all("ts" in e and e["ticket"] == 7 for e in events)


def test_caller_ts_and_ticket_not_clobbered(tmp_path):
    episodic.append_event(7, {"type": "a", "ts": 123.0}, runs_root=str(tmp_path))
    ev = episodic.read_events(7, runs_root=str(tmp_path))[0]
    assert ev["ts"] == 123.0


def test_read_skips_malformed_lines(tmp_path):
    episodic.append_event(9, {"type": "good"}, runs_root=str(tmp_path))
    with open(episodic.episode_path(9, str(tmp_path)), "a") as f:
        f.write("{not json\n")
        f.write('"a bare string"\n')
    episodic.append_event(9, {"type": "also_good"}, runs_root=str(tmp_path))
    events = episodic.read_events(9, runs_root=str(tmp_path))
    assert [e["type"] for e in events] == ["good", "also_good"]


def test_read_missing_episode_is_empty(tmp_path):
    assert episodic.read_events(999, runs_root=str(tmp_path)) == []


def test_non_dict_event_rejected(tmp_path):
    try:
        episodic.append_event(1, ["not", "a", "dict"], runs_root=str(tmp_path))
    except TypeError:
        return
    raise AssertionError("expected TypeError")


def test_events_are_single_json_lines(tmp_path):
    episodic.append_event(3, {"type": "x", "text": "multi\nline"},
                          runs_root=str(tmp_path))
    with open(episodic.episode_path(3, str(tmp_path))) as f:
        lines = f.read().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["text"] == "multi\nline"
