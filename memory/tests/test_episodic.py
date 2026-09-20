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


# --- wq#1635: deterministic digest + archival commit + sweep ---------------


def _attempt(n, classification, **kw):
    ev = {"type": "attempt_end", "attempt": n, "classification": classification}
    ev.update(kw)
    return ev


def test_digest_none_without_attempt_end():
    assert episodic.digest_events(5, [{"type": "note"}]) is None
    assert episodic.digest_events(5, []) is None


def test_digest_success_shape():
    d = episodic.digest_events(1635, [
        _attempt(1, "HANG", killed_reason="wall_clock"),
        _attempt(2, "SUCCESS", title="fix the thing", lane="foreman",
                 result_text="Landed http://100.121.17.5:3000/l/f/pulls"
                             " (PR http://100.121.17.5:3000/l/f/pull/9) green"),
    ])
    assert d["summary"] == "wq#1635 attempt 2 SUCCESS: fix the thing"
    assert d["changed"] == ["http://100.121.17.5:3000/l/f/pull/9"]
    assert d["failed"] == ["attempt 1: HANG (wall_clock)"]
    assert d["kind"] == "code-ship"
    assert "wq1635" in d["tags"] and "foreman" in d["tags"]
    assert d["why"].startswith("Landed")


def test_digest_failure_kind_and_why_fallback():
    d = episodic.digest_events(7, [
        _attempt(1, "INDETERMINATE", assistant_tail=["last words"]),
    ])
    assert d["kind"] == "session"
    assert d["why"] == "last words"
    assert d["failed"] == ["attempt 1: INDETERMINATE"]


def test_digest_items_never_contain_cli_separators():
    # lumen_episode.py's CLI re-splits list args on , and ;
    d = episodic.digest_events(7, [
        _attempt(1, "OOM", killed_reason="rss,limit;hit", lane="a;b"),
    ])
    for item in d["failed"] + d["tags"]:
        assert "," not in item and ";" not in item


def test_commit_to_archive_invokes_cli(tmp_path, monkeypatch):
    episodic.append_event(3, _attempt(1, "SUCCESS", title="t"),
                          runs_root=str(tmp_path))
    calls = []
    monkeypatch.setattr(episodic.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd))
    d = episodic.commit_to_archive(3, runs_root=str(tmp_path),
                                   episode_bin="/fake/lumen_episode.py")
    assert d["summary"].startswith("wq#3 attempt 1 SUCCESS")
    (cmd,) = calls
    assert cmd[1] == "/fake/lumen_episode.py" and cmd[2] == "commit"
    assert cmd[cmd.index("--summary") + 1] == d["summary"]


def test_commit_to_archive_noop_without_events(tmp_path, monkeypatch):
    monkeypatch.setattr(episodic.subprocess, "run",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError))
    assert episodic.commit_to_archive(99, runs_root=str(tmp_path)) is None


def test_sweep_removes_only_old_episodes(tmp_path):
    old = episodic.append_event(1, {"type": "a"}, runs_root=str(tmp_path))
    new = episodic.append_event(2, {"type": "a"}, runs_root=str(tmp_path))
    stale = episodic.time.time() - 31 * 86400
    os.utime(old, (stale, stale))
    removed = episodic.sweep_episodes(days=30, runs_root=str(tmp_path))
    assert removed == [old]
    assert not os.path.exists(old) and os.path.exists(new)
