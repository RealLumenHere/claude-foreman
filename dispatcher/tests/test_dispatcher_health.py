"""dispatcher_health.py — dispatcher-not-dispatching alarm unit tests (wq#825).

Zero quota, zero live services: dispatcher.log fixtures are hand-written JSONL (same
shape foreman_dispatcher.DecisionLog.emit writes), ntfy is a fake shim via
notify_sinks.NTFY_BIN. Every alarm test asserts on the fake ntfy transcript directly —
"an alarm fires within 2 cycles" (the ticket's own acceptance test) is proven by
actually counting ntfy calls, not by trusting the code read right.
"""
import json
import os
import stat
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dispatcher_health as dh  # noqa: E402
import notify_sinks             # noqa: E402


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def henv(tmp_path, monkeypatch):
    log_path = tmp_path / "dispatcher.log"
    ntfy_log = tmp_path / "ntfy.jsonl"
    fake_ntfy = _script(tmp_path / "fake-ntfy",
                        f"#!/usr/bin/env bash\necho \"$@\" >> {ntfy_log}\n")

    monkeypatch.setattr(dh, "LOG_PATH", str(log_path))
    monkeypatch.setattr(dh, "STATE_PATH", str(tmp_path / "dispatcher_health" / "state.json"))
    monkeypatch.setattr(dh, "CONSECUTIVE_THRESHOLD", 2)
    monkeypatch.setattr(dh, "NTFY_DEDUP_HOURS", 1)
    monkeypatch.setattr(notify_sinks, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(notify_sinks, "DESKTOP_SSH_HOST", "")

    return {"tmp": tmp_path, "log_path": log_path, "ntfy_log": ntfy_log}


def ntfy_lines(henv):
    if not henv["ntfy_log"].exists():
        return []
    return henv["ntfy_log"].read_text().splitlines()


def write_pass(log_path, pass_id, events):
    """events: [{"event": "...", ...fields}]. Appends one JSONL line per event, same
    shape DecisionLog.emit writes (ts + pass_id + event + fields)."""
    with open(log_path, "a") as f:
        for e in events:
            rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "pass_id": pass_id}
            rec.update(e)
            f.write(json.dumps(rec) + "\n")


def config_error_pass(pass_id):
    return [{"event": "pass_start"},
            {"event": "error", "stage": "config", "reason": "bad user-lane value"}]


def zero_spawn_pass(pass_id, candidates=3):
    return [{"event": "pass_start"},
            {"event": "pass_end", "spawned": 0, "candidates": candidates, "tight": False}]


def healthy_pass(pass_id, spawned=2, candidates=2):
    return [{"event": "pass_start"},
            {"event": "spawn", "ticket": 1},
            {"event": "pass_end", "spawned": spawned, "candidates": candidates,
             "tight": False}]


def halt_pass(pass_id, reason="lock_busy"):
    return [{"event": "halt", "stage": "lock", "reason": reason}]


# ---- iter_passes / pass_is_unhealthy ----------------------------------------------------

def test_iter_passes_groups_by_pass_id(henv):
    write_pass(henv["log_path"], "p1", healthy_pass("p1"))
    write_pass(henv["log_path"], "p2", config_error_pass("p2"))

    passes = dh.iter_passes(henv["log_path"])

    assert [pid for pid, _ in passes] == ["p1", "p2"]
    assert len(passes[0][1]) == 3 and len(passes[1][1]) == 2


def test_iter_passes_skips_corrupt_lines(henv):
    write_pass(henv["log_path"], "p1", healthy_pass("p1"))
    with open(henv["log_path"], "a") as f:
        f.write("{not json\n")

    passes = dh.iter_passes(henv["log_path"])
    assert len(passes) == 1


def test_iter_passes_missing_log_returns_empty(henv):
    assert dh.iter_passes(str(henv["tmp"] / "nope.log")) == []


def test_pass_is_unhealthy_on_error_event():
    unhealthy, reason = dh.pass_is_unhealthy(config_error_pass("p1"))
    assert unhealthy and "stage=config" in reason


def test_pass_is_unhealthy_on_zero_spawn_with_candidates():
    unhealthy, reason = dh.pass_is_unhealthy(zero_spawn_pass("p1", candidates=5))
    assert unhealthy and "candidates=5" in reason


def test_pass_healthy_when_spawned_zero_and_no_candidates():
    """spawned=0 with candidates=0 is just an empty queue — not a failure."""
    unhealthy, _ = dh.pass_is_unhealthy(zero_spawn_pass("p1", candidates=0))
    assert not unhealthy


def test_pass_healthy_when_spawns_happened():
    unhealthy, _ = dh.pass_is_unhealthy(healthy_pass("p1"))
    assert not unhealthy


def test_halt_pass_is_not_unhealthy():
    """kill_switch / lock_busy / circuit_breaker halts are intentional or already
    alarmed elsewhere — they must not read as a dispatcher failure."""
    unhealthy, _ = dh.pass_is_unhealthy(halt_pass("p1"))
    assert not unhealthy


# ---- recent_unhealthy_streak -------------------------------------------------------------

def test_streak_counts_consecutive_unhealthy_from_most_recent(henv):
    write_pass(henv["log_path"], "p1", healthy_pass("p1"))
    write_pass(henv["log_path"], "p2", config_error_pass("p2"))
    write_pass(henv["log_path"], "p3", config_error_pass("p3"))

    streak, most_recent, reasons = dh.recent_unhealthy_streak(henv["log_path"])

    assert streak == 2 and most_recent == "p3" and len(reasons) == 2


def test_streak_stops_at_healthy_pass(henv):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    write_pass(henv["log_path"], "p2", healthy_pass("p2"))
    write_pass(henv["log_path"], "p3", config_error_pass("p3"))

    streak, most_recent, _ = dh.recent_unhealthy_streak(henv["log_path"])

    assert streak == 1 and most_recent == "p3"  # p2 breaks the streak from p1


def test_streak_stops_at_halt_pass(henv):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    write_pass(henv["log_path"], "p2", halt_pass("p2"))

    streak, _, _ = dh.recent_unhealthy_streak(henv["log_path"])
    assert streak == 0


def test_streak_zero_on_empty_log(henv):
    streak, most_recent, reasons = dh.recent_unhealthy_streak(henv["log_path"])
    assert streak == 0 and most_recent is None and reasons == []


# ---- check_dispatcher_health / ntfy ------------------------------------------------------

def test_no_alarm_below_threshold(henv):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))  # only 1 < threshold 2

    alarmed, info = dh.check_dispatcher_health()

    assert not alarmed and info["streak"] == 1
    assert ntfy_lines(henv) == []


def test_alarm_fires_within_two_consecutive_config_error_passes(henv):
    """The ticket's own acceptance test: inject a config error, prove an alarm fires
    within 2 cycles."""
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    alarmed1, _ = dh.check_dispatcher_health()
    assert not alarmed1
    assert ntfy_lines(henv) == []

    write_pass(henv["log_path"], "p2", config_error_pass("p2"))
    alarmed2, info2 = dh.check_dispatcher_health()

    assert alarmed2 and info2["streak"] == 2
    lines = ntfy_lines(henv)
    assert len(lines) == 1
    assert "-p urgent" in lines[0]
    assert "NOT DISPATCHING" in lines[0]


def test_alarm_fires_on_zero_spawn_with_candidates_streak(henv):
    write_pass(henv["log_path"], "p1", zero_spawn_pass("p1", candidates=4))
    write_pass(henv["log_path"], "p2", zero_spawn_pass("p2", candidates=6))

    alarmed, info = dh.check_dispatcher_health()

    assert alarmed and info["streak"] == 2
    assert len(ntfy_lines(henv)) == 1


def test_dry_run_reports_truth_without_side_effects(henv):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    write_pass(henv["log_path"], "p2", config_error_pass("p2"))

    alarmed, info = dh.check_dispatcher_health(dry_run=True)

    assert alarmed and info.get("would_ntfy") is True
    assert ntfy_lines(henv) == []
    assert not os.path.exists(dh.STATE_PATH)


def test_ntfy_deduped_within_window(henv):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    write_pass(henv["log_path"], "p2", config_error_pass("p2"))
    dh.check_dispatcher_health()
    assert len(ntfy_lines(henv)) == 1

    write_pass(henv["log_path"], "p3", config_error_pass("p3"))
    alarmed, info = dh.check_dispatcher_health()

    assert alarmed and info.get("deduped") is True
    assert len(ntfy_lines(henv)) == 1  # no second ntfy


def test_ntfy_reping_after_dedup_window_elapses(henv):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    write_pass(henv["log_path"], "p2", config_error_pass("p2"))
    now = datetime.now(timezone.utc)
    dh.check_dispatcher_health(now=now)
    assert len(ntfy_lines(henv)) == 1

    write_pass(henv["log_path"], "p3", config_error_pass("p3"))
    later = now + timedelta(hours=2)
    alarmed, info = dh.check_dispatcher_health(now=later)

    assert alarmed and not info.get("deduped")
    assert len(ntfy_lines(henv)) == 2


def test_healthy_pass_resets_alarm_state_naturally(henv):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    write_pass(henv["log_path"], "p2", config_error_pass("p2"))
    dh.check_dispatcher_health()
    assert len(ntfy_lines(henv)) == 1

    write_pass(henv["log_path"], "p3", healthy_pass("p3"))
    alarmed, info = dh.check_dispatcher_health()

    assert not alarmed and info["streak"] == 0


# ---- CLI ----------------------------------------------------------------------------------

def test_cmd_status_zero_side_effects(henv, capsys):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))

    assert dh.main(["status"]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["streak"] == 1
    assert ntfy_lines(henv) == []


def test_cmd_check_fires_alarm(henv, capsys):
    write_pass(henv["log_path"], "p1", config_error_pass("p1"))
    write_pass(henv["log_path"], "p2", config_error_pass("p2"))

    assert dh.main(["check"]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["alarmed"] is True
    assert len(ntfy_lines(henv)) == 1
