"""vlogs_mirror.py — journal mirror for ntfy pages, unit tests (wq#1416).

vlogs-forwarder.service itself (journal -> victorialogs) is fleet infra outside this
repo, so these tests only cover the achievable-from-this-repo half: every alert mirror()
sees becomes one greppable JSON line on stderr (what a systemd unit's journal captures),
and mirror() never raises regardless of what garbage a caller hands it.
"""
import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import vlogs_mirror  # noqa: E402


def test_mirror_emits_one_json_line_on_stderr(capsys):
    vlogs_mirror.mirror("dispatch_health", "[DISPATCH] DISPATCHER STALLED",
                         "urgent", "3 consecutive unhealthy passes")
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "ntfy_publish"
    assert record["source"] == "dispatch_health"
    assert record["title"] == "[DISPATCH] DISPATCHER STALLED"
    assert record["priority"] == "urgent"
    assert record["msg"] == "3 consecutive unhealthy passes"
    assert "ts" in record


def test_mirror_never_raises_on_unserializable_payload(capsys):
    vlogs_mirror.mirror("self_heal", "title", "default", object())
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
