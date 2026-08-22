"""self_heal.py — wq self-heal daemon unit tests (wq#725 AUTO-7).

Zero quota, zero live services: the real `wq`/`slock` binaries run against a scratch
WQ_DB sqlite file (same fixture pattern as test_dispatcher.py); ntfy is a fake shim.
Every test asserts on both the decision log AND the ticket's real wq status — a "not
released" claim must be proven by re-reading wq, not just by an absent log line.
"""
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import self_heal as sh  # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"
SLOCK_BIN = "/home/lumen/bin/slock"


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def henv(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ntfy_log = tmp_path / "ntfy.jsonl"
    fake_ntfy = _script(bin_dir / "fake-ntfy",
                        f"#!/usr/bin/env bash\necho \"$@\" >> {ntfy_log}\n")
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    wq_db = tmp_path / "scratch_wq.db"

    monkeypatch.setenv("WQ_DB", str(wq_db))
    monkeypatch.setenv("WQ_AGENT", "test-fworker-agent")  # ticket "owner" for setup

    monkeypatch.setattr(sh, "WQ_BIN", WQ_BIN)
    monkeypatch.setattr(sh, "SLOCK_BIN", SLOCK_BIN)
    monkeypatch.setattr(sh, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(sh, "STATE_PATH", str(tmp_path / "self_heal" / "state.json"))
    monkeypatch.setattr(sh, "LOG_PATH", str(tmp_path / "self_heal.log"))
    monkeypatch.setattr(sh, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(sh, "DISARM_PATH", str(tmp_path / "DISARM"))
    monkeypatch.setattr(sh, "STOP_PATH", str(tmp_path / "STOP"))

    return {"tmp": tmp_path, "wq_db": str(wq_db), "ntfy_log": ntfy_log,
            "runs_root": runs_root}


# ---- helpers ---------------------------------------------------------------------------

def add_ticket(title="scratch self-heal ticket"):
    p = subprocess.run([WQ_BIN, "add", "--title", title], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def block_ticket(tid, reason):
    p = subprocess.run([WQ_BIN, "claim", "--id", str(tid)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    p = subprocess.run([WQ_BIN, "block", str(tid), reason], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def wq_show_field(tid, field):
    p = subprocess.run([WQ_BIN, "show", str(tid)], capture_output=True, text=True)
    for line in p.stdout.splitlines():
        k, _, v = line.partition(":")
        if k.strip() == field:
            return v.strip()
    return None


def make_attempts(henv, tid, n):
    d = henv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (d / f"attempt-{i}.json").write_text("{}")


def log_events(henv, event=None, ticket=None):
    path = henv["tmp"] / "self_heal.log"
    if not path.exists():
        return []
    evs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if event is not None:
        evs = [e for e in evs if e["event"] == event]
    if ticket is not None:
        evs = [e for e in evs if e.get("ticket") == ticket]
    return evs


def ntfy_lines(henv):
    if not henv["ntfy_log"].exists():
        return []
    return henv["ntfy_log"].read_text().splitlines()


def set_blocked_at(henv, tid, when):
    """Backdate wq's own blocked_at column (wq#741) to simulate an old block — there's no
    wq CLI for this (it's system-managed, set only by cmd_block), so go straight at the
    scratch DB the same way the fixture already owns it."""
    con = sqlite3.connect(henv["wq_db"])
    con.execute("UPDATE tasks SET blocked_at=? WHERE id=?",
                (when.strftime("%Y-%m-%d %H:%M:%S"), tid))
    con.commit()
    con.close()


def clear_block_reason_column(henv, tid):
    """Simulate a ticket blocked before wq#741 shipped: notes carries the class prefix but
    the (newer) block_reason column is NULL."""
    con = sqlite3.connect(henv["wq_db"])
    con.execute("UPDATE tasks SET block_reason=NULL WHERE id=?", (tid,))
    con.commit()
    con.close()


# ---- INFRA_CRASH: healed --------------------------------------------------------------

def test_infra_crash_auto_released_within_cap(henv):
    tid = add_ticket()
    block_ticket(tid, "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x")
    make_attempts(henv, tid, 2)

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "open"
    heals = log_events(henv, "heal", ticket=tid)
    assert len(heals) == 1 and heals[0]["attempts"] == 2
    assert ntfy_lines(henv) == []  # a healthy heal never pages Frank


def test_infra_crash_healed_via_notes_fallback_pre_wq741(henv):
    """A ticket blocked before wq#741 added block_reason has the prefix only in notes —
    classify_block() must still recognize it so upgrading self_heal.py never strands an
    in-flight INFRA_CRASH."""
    tid = add_ticket()
    block_ticket(tid, "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x")
    clear_block_reason_column(henv, tid)
    make_attempts(henv, tid, 2)

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "open"
    heals = log_events(henv, "heal", ticket=tid)
    assert len(heals) == 1 and heals[0]["attempts"] == 2


# ---- WORK_BLOCKED / LIMIT_HIT / unmarked: never released -------------------------------

@pytest.mark.parametrize("reason,klass", [
    ("WORK_BLOCKED: REFUSED after 2 attempts; transcript=x", "WORK_BLOCKED"),
    ("LIMIT_HIT: TURNS after 2 attempts; transcript=x", "LIMIT_HIT"),
    ("CRASH after 2 attempts; transcript=x", "unmarked"),  # pre-wq#725 legacy reason
])
def test_non_infra_never_released(henv, reason, klass):
    tid = add_ticket()
    block_ticket(tid, reason)
    make_attempts(henv, tid, 1)

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "blocked"
    skips = log_events(henv, "skip", ticket=tid)
    assert len(skips) == 1
    assert skips[0]["reason"] == "not_infra_crash"
    assert skips[0]["klass"] == klass
    assert log_events(henv, "heal", ticket=tid) == []


# ---- attempt cap ------------------------------------------------------------------------

def test_heal_attempts_exhausted_notifies_and_leaves_blocked(henv):
    tid = add_ticket()
    block_ticket(tid, "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x")
    make_attempts(henv, tid, sh.MAX_HEAL_ATTEMPTS)  # already at the cap

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "blocked"
    skip = log_events(henv, "skip", ticket=tid)
    assert len(skip) == 1 and skip[0]["reason"] == "heal_attempts_exhausted"
    lines = ntfy_lines(henv)
    assert len(lines) == 1 and f"wq#{tid}" in lines[0]

    # second pass same run: dedup ledger suppresses a repeat ntfy
    assert sh.run_pass() == 0
    assert len(ntfy_lines(henv)) == 1


# ---- staleness (wq#741: age comes from wq's own blocked_at column) --------------------

def test_stale_infra_crash_notifies_and_leaves_blocked(henv):
    tid = add_ticket()
    block_ticket(tid, "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x")
    make_attempts(henv, tid, 1)
    old = (datetime.now(timezone.utc) - timedelta(minutes=sh.STALE_AFTER_MIN + 5))
    set_blocked_at(henv, tid, old)

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "blocked"
    skip = log_events(henv, "skip", ticket=tid)
    assert len(skip) == 1 and skip[0]["reason"] == "stale_infra_crash"
    assert len(ntfy_lines(henv)) == 1


# ---- dry-run: zero side effects ---------------------------------------------------------

def test_dry_run_no_side_effects(henv):
    tid = add_ticket()
    block_ticket(tid, "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x")
    make_attempts(henv, tid, 1)

    assert sh.run_pass(dry_run=True) == 0

    assert wq_show_field(tid, "status") == "blocked"
    assert log_events(henv, "would_heal", ticket=tid) != []
    assert log_events(henv, "heal", ticket=tid) == []
    assert not (henv["tmp"] / "self_heal" / "state.json").exists()


# ---- kill switch / single instance -------------------------------------------------------

def test_disarm_halts_before_any_scan(henv):
    tid = add_ticket()
    block_ticket(tid, "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x")
    make_attempts(henv, tid, 1)
    (henv["tmp"] / "DISARM").write_text("")

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "blocked"
    halts = log_events(henv, "halt")
    assert len(halts) == 1 and halts[0]["stage"] == "kill_switch"
    assert log_events(henv, ticket=tid) == []


def test_units_staged_disabled():
    sysd = os.path.join(DISPATCHER_DIR, "systemd")
    service = open(os.path.join(sysd, "lumen-foreman-selfheal.service")).read()
    timer = open(os.path.join(sysd, "lumen-foreman-selfheal.timer")).read()
    assert "Type=oneshot" in service
    assert "run --once" in service
    assert not re.search(r"^\[Install\]", service, re.M)  # timer-start only
    assert "OnUnitActiveSec=10min" in timer
    assert "Persistent=false" in timer
    assert "OnBootSec" in timer


def test_slock_busy_noop(henv):
    p = subprocess.run([SLOCK_BIN, "acquire", sh.SLOCK_SCOPE, "--ttl", "5",
                        "--holder", "another-selfheal"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr

    assert sh.run_pass() == 0

    halts = log_events(henv, "halt")
    assert len(halts) == 1 and halts[0]["reason"] == "lock_busy"
    assert log_events(henv, "pass_start") == []
