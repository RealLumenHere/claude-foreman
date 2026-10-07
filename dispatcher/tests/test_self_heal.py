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
import local_capacity           # noqa: E402
import self_heal as sh          # noqa: E402
import notify_sinks             # noqa: E402

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
    # wq#3539: self_heal reads the trip-cause sidecar via circuit_breaker's own
    # DISARM_PATH, so keep the two in sync under test the same way they're the same
    # env var (FOREMAN_DISPATCH_DISARM) in production.
    monkeypatch.setattr(sh.circuit_breaker, "DISARM_PATH", str(tmp_path / "DISARM"))
    monkeypatch.setattr(notify_sinks, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(notify_sinks, "DESKTOP_SSH_HOST", "")

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

# ---- wq#1496: self_heal.wq_show() duplicated router.wq_show()'s plain-text `wq show`
# parser and the same bug — embedded newlines survive only as the literal backslash-n
# escape (wq#1277), so any multi-line notes/detail field this module read was mangled.
# Drives the real wq binary; a hand-built dict would hide exactly this bug. ------------

def test_wq_show_returns_real_newlines(henv):
    detail = "line one\n\nline three after a blank"
    tid = add_ticket("wq1496 self_heal multiline")
    p = subprocess.run([WQ_BIN, "edit", str(tid), "--detail", detail], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    t = sh.wq_show(tid)
    assert t.get("detail") == detail, (
        f"self_heal.wq_show() mangled the multi-line detail — got {t.get('detail')!r}")


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


# ---- wq#2129 (DISPATCH_REBUILD_SPEC step 5): RUNG_FAULT + charged_only -------------------

RUNG_REASON = "RUNG_FAULT: HANG after 0 turns on local-worker; transcript=x"


@pytest.fixture
def rung_env(henv, monkeypatch):
    """A `local_backends` config, a cooldown file path, and a probe stub."""
    cfg = henv["tmp"] / "config.json"
    cfg.write_text(json.dumps({"local_backends": {
        "qwen-local": {"health_url": "http://qwen.invalid/health"}}}))
    cooldown = henv["tmp"] / "backend_cooldown.json"
    probes = []
    monkeypatch.setattr(sh, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(sh, "BACKEND_COOLDOWN_PATH", str(cooldown))
    state = {"up": True}
    monkeypatch.setattr(sh, "backend_answers",
                        lambda url: probes.append(url) or state["up"])
    return {"cooldown": cooldown, "probes": probes, "state": state}


def pin_model(henv, tid, model):
    con = sqlite3.connect(henv["wq_db"])
    con.execute("UPDATE tasks SET model=? WHERE id=?", (model, tid))
    con.commit()
    con.close()


def test_classify_block_learns_rung_fault(henv):
    tid = add_ticket()
    block_ticket(tid, RUNG_REASON)
    klass, detail = sh.classify_block(sh.wq_show(tid))
    assert klass == "RUNG_FAULT" and detail.startswith("HANG after 0 turns")


def test_rung_fault_released_when_backend_healthy(henv, rung_env):
    tid = add_ticket()
    pin_model(henv, tid, "qwen-local")
    block_ticket(tid, RUNG_REASON)

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "open"
    assert rung_env["probes"] == ["http://qwen.invalid/health"]
    assert len(log_events(henv, "heal", ticket=tid)) == 1


def test_rung_fault_release_ignores_block_age(henv, rung_env):
    from datetime import timedelta
    tid = add_ticket()
    pin_model(henv, tid, "qwen-local")
    block_ticket(tid, RUNG_REASON)
    set_blocked_at(henv, tid, sh._utcnow() - timedelta(hours=6))  # far past STALE_AFTER_MIN

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "open"
    assert ntfy_lines(henv) == []


def test_rung_fault_held_while_backend_in_cooldown(henv, rung_env):
    tid = add_ticket()
    pin_model(henv, tid, "qwen-local")
    block_ticket(tid, RUNG_REASON)
    until = sh._utcnow().timestamp() + 900
    rung_env["cooldown"].write_text(json.dumps({"qwen-local": {"until": until}}))

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "blocked"
    assert log_events(henv, "skip", ticket=tid)[0]["reason"] == "rung_backend_in_cooldown"
    assert rung_env["probes"] == []  # cooldown is checked before the network


def test_rung_fault_released_once_the_cooldown_lapsed(henv, rung_env):
    tid = add_ticket()
    pin_model(henv, tid, "qwen-local")
    block_ticket(tid, RUNG_REASON)
    rung_env["cooldown"].write_text(
        json.dumps({"qwen-local": {"until": sh._utcnow().timestamp() - 5}}))

    assert sh.run_pass() == 0
    assert wq_show_field(tid, "status") == "open"


@pytest.mark.parametrize("entry_key, entry_shape, expected", [
    ("qwen-local", "epoch", False),          # bare epoch, lapsed
    ("qwen-local", "epoch_future", True),    # bare epoch, future
    ("qwen-local", "dict", False),           # {"until"}, lapsed
    ("qwen-local", "dict_future", True),     # {"until"}, future
    ("qwen-local@low", "dict", False),       # @effort key, lapsed
    ("qwen-local@low", "dict_future", True), # @effort key, future
])
def test_backend_in_cooldown_matches_local_capacity_reader(
        rung_env, entry_key, entry_shape, expected):
    now = sh._utcnow().timestamp()
    until = now + 3600 if entry_shape.endswith("_future") else now - 3600
    entry = until if entry_shape.startswith("epoch") else {"until": until}
    rung_env["cooldown"].write_text(json.dumps({entry_key: entry}))

    assert sh.backend_in_cooldown("qwen-local") is expected
    assert ("qwen-local" in local_capacity.cooled_down_backends(
        now=now, path=str(rung_env["cooldown"]))) is expected


def test_rung_fault_held_while_health_url_does_not_answer(henv, rung_env):
    tid = add_ticket()
    pin_model(henv, tid, "qwen-local")
    block_ticket(tid, RUNG_REASON)
    rung_env["state"]["up"] = False

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "blocked"
    assert log_events(henv, "skip", ticket=tid)[0]["reason"] == "rung_backend_unhealthy"


def test_rung_fault_held_when_backend_cannot_be_resolved(henv, rung_env):
    tid = add_ticket()  # no pinned model, no attempt record naming one
    block_ticket(tid, RUNG_REASON)

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "blocked"
    assert log_events(henv, "skip", ticket=tid)[0]["reason"] == "rung_backend_unresolved"


def test_rung_fault_backend_falls_back_to_the_latest_attempt_model(henv, rung_env):
    tid = add_ticket()
    block_ticket(tid, RUNG_REASON)
    d = henv["runs_root"] / f"wq{tid}"
    d.mkdir()
    (d / "attempt-1.json").write_text(json.dumps({"model": "qwen-local"}))

    assert sh.run_pass() == 0
    assert wq_show_field(tid, "status") == "open"


def test_rung_fault_dry_run_does_not_release(henv, rung_env):
    tid = add_ticket()
    pin_model(henv, tid, "qwen-local")
    block_ticket(tid, RUNG_REASON)

    assert sh.run_pass(dry_run=True) == 0

    assert wq_show_field(tid, "status") == "blocked"
    assert len(log_events(henv, "would_heal", ticket=tid)) == 1


def test_attempt_count_charged_only(henv, monkeypatch):
    d = henv["runs_root"] / "wq7"
    d.mkdir()
    for i, rec in enumerate([{}, {"charged_to": "ticket"}, {"charged_to": "rung"},
                             {"charged_to": "harness"}, {"charged_to": "budget"}], 1):
        (d / f"attempt-{i}.json").write_text(json.dumps(rec))
    (d / "attempt-9.json").write_text("not json")  # unreadable = ticket, like a bare name
    assert sh.attempt_count(7) == 6
    assert sh.attempt_count(7, charged_only=True) == 3
    monkeypatch.setenv("FOREMAN_CHARGE_MODE", "legacy")
    assert sh.attempt_count(7, charged_only=True) == 6


def test_infra_crash_cap_ignores_rung_charged_attempts(henv):
    tid = add_ticket()
    block_ticket(tid, "INFRA_CRASH: LAUNCH_FAILURE after 2 attempts; transcript=x")
    d = henv["runs_root"] / f"wq{tid}"
    d.mkdir()
    for i in range(1, sh.MAX_HEAL_ATTEMPTS + 1):
        (d / f"attempt-{i}.json").write_text(json.dumps({"charged_to": "rung"}))

    assert sh.run_pass() == 0

    assert wq_show_field(tid, "status") == "open"
    assert log_events(henv, "heal", ticket=tid)[0]["attempts"] == 0


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


# ---- auth re-arm (wq#3539) ---------------------------------------------------------------

def write_trip_cause(henv, cause):
    (henv["tmp"] / "DISARM.cause.json").write_text(json.dumps({"cause": cause}))


def test_auth_rearm_clears_disarm_when_probe_passes(henv, monkeypatch):
    (henv["tmp"] / "DISARM").write_text("")
    write_trip_cause(henv, "auth")
    monkeypatch.setattr(sh.cli_canary, "probe", lambda model, bin_path=None: (True, "ok", None))

    assert sh.run_pass() == 0

    assert not (henv["tmp"] / "DISARM").exists()
    assert not (henv["tmp"] / "DISARM.cause.json").exists()
    assert log_events(henv, "rearm") != []
    lines = ntfy_lines(henv)
    assert any("RE-ARMED" in l for l in lines)


def test_auth_rearm_leaves_disarm_when_probe_still_fails(henv, monkeypatch):
    (henv["tmp"] / "DISARM").write_text("")
    write_trip_cause(henv, "auth")
    monkeypatch.setattr(sh.cli_canary, "probe",
                        lambda model, bin_path=None: (False, "still disabled", None))

    assert sh.run_pass() == 0

    assert (henv["tmp"] / "DISARM").exists()
    halts = log_events(henv, "halt")
    assert len(halts) == 1 and halts[0]["stage"] == "kill_switch"


def test_auth_rearm_skipped_when_trip_cause_is_infra(henv, monkeypatch):
    (henv["tmp"] / "DISARM").write_text("")
    write_trip_cause(henv, "infra")
    probed = []
    monkeypatch.setattr(sh.cli_canary, "probe",
                        lambda model, bin_path=None: probed.append(1) or (True, "ok", None))

    assert sh.run_pass() == 0

    assert (henv["tmp"] / "DISARM").exists()
    assert probed == []
    skips = log_events(henv, "skip")
    assert any(s.get("stage") == "auth_rearm" and s.get("reason") == "trip_cause_not_auth"
              for s in skips)


def test_auth_rearm_skipped_when_no_cause_file(henv):
    # same shape test_disarm_halts_before_any_scan already covers end-to-end; this
    # pins the specific auth_rearm skip reason it goes through to get there.
    (henv["tmp"] / "DISARM").write_text("")

    assert sh.run_pass() == 0

    skips = log_events(henv, "skip")
    assert any(s.get("stage") == "auth_rearm" and s.get("cause") is None for s in skips)


def test_auth_rearm_dry_run_never_probes_or_clears(henv, monkeypatch):
    (henv["tmp"] / "DISARM").write_text("")
    write_trip_cause(henv, "auth")
    probed = []
    monkeypatch.setattr(sh.cli_canary, "probe",
                        lambda model, bin_path=None: probed.append(1) or (True, "ok", None))

    assert sh.run_pass(dry_run=True) == 0

    assert probed == []
    assert (henv["tmp"] / "DISARM").exists()
    assert log_events(henv, "would_probe") != []


def test_auth_rearm_dedup_skips_second_probe_within_window(henv, monkeypatch):
    (henv["tmp"] / "DISARM").write_text("")
    write_trip_cause(henv, "auth")
    probed = []

    def fake_probe(model, bin_path=None):
        probed.append(1)
        return False, "still disabled", None
    monkeypatch.setattr(sh.cli_canary, "probe", fake_probe)

    assert sh.run_pass() == 0
    assert len(probed) == 1

    assert sh.run_pass() == 0
    assert len(probed) == 1  # deduped: no second probe this soon
    skips = log_events(henv, "skip")
    assert any(s.get("stage") == "auth_rearm" and s.get("reason") == "probe_deduped"
              for s in skips)


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

