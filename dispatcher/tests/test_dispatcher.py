"""foreman_dispatcher.py — safety-gate unit tests (wq#715 AUTO-3, DESIGN.md §11).

Zero quota, zero live services: the real `wq`/`slock` binaries run against a scratch
WQ_DB sqlite file; `lumen-run`, governor, and ntfy are the fake shims the harness tests
already use (harness/tests/fake_*.py); Langfuse is a nonexistent env file (log-only
degradation). Adversarial posture per DESIGN §11: every gate test asserts the negative
too — the lumen-run call log records zero spawns, not just a "skip" line.
"""
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
HARNESS_TESTS = os.path.join(REPO, "harness", "tests")
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "orchestrator"))
sys.path.insert(0, os.path.join(REPO, "harness"))
import foreman_dispatcher as fd  # noqa: E402
import concurrency  # noqa: E402
import router  # noqa: E402
import spawner  # noqa: E402
import pacing  # noqa: E402
import circuit_breaker  # noqa: E402
import runaway  # noqa: E402
import self_heal  # noqa: E402
import notify_sinks  # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"
SLOCK_BIN = "/home/lumen/bin/slock"
FAKE_GOVERNOR = os.path.join(HARNESS_TESTS, "fake_governor.py")
FAKE_LUMEN_RUN = os.path.join(HARNESS_TESTS, "fake_lumen_run.py")

HEALTHY_GOV = {"limits": {"session": {"percent": 10}, "weekly_all": {"percent": 10},
                          "fable_weekly_scoped": {"percent": 10}}}
# session remaining 30%, floor 20, soft margin 15 -> 'tight' but not 'deny'
TIGHT_GOV = {"limits": {"session": {"percent": 70}, "weekly_all": {"percent": 10},
                        "fable_weekly_scoped": {"percent": 10}}}

BASE_CONFIG = {
    "max_spawns_per_pass": 3,
    "cloud_ceiling": 3,
    "tight_mode": {"max_spawns_per_pass": 1, "min_pri": 1},
    "lanes": {
        "testlane": {"enabled": True, "max_per_day": 10},
        "fleet-fires": {"enabled": True, "max_per_day": 10, "requires_approval": True},
    },
}


def _script(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def denv(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    approvals = tmp_path / "approvals"
    approvals.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    lumen_run = _script(bin_dir / "lumen-run",
                        f"#!/usr/bin/env bash\nexec python3 {FAKE_LUMEN_RUN} \"$@\"\n")
    ntfy_log = tmp_path / "ntfy.jsonl"
    fake_ntfy = _script(bin_dir / "fake-ntfy",
                        f"#!/usr/bin/env bash\necho \"$@\" >> {ntfy_log}\n")

    units_state = tmp_path / "units.json"
    units_state.write_text("[]")
    calls_log = tmp_path / "lumen_run_calls.jsonl"
    gov_state = tmp_path / "gov_state.json"
    gov_state.write_text(json.dumps(HEALTHY_GOV))
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(BASE_CONFIG))
    wq_db = tmp_path / "scratch_wq.db"

    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_LUMEN_RUN_STATE", str(units_state))
    monkeypatch.setenv("FAKE_LUMEN_RUN_LOG", str(calls_log))
    monkeypatch.setenv("WQ_DB", str(wq_db))
    monkeypatch.setenv("WQ_AGENT", "test-dispatcher")
    monkeypatch.delenv("FAKE_GOVERNOR_DENY", raising=False)

    monkeypatch.setattr(fd, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(fd, "STATE_PATH", str(tmp_path / "dstate.json"))
    monkeypatch.setattr(fd, "LOG_PATH", str(tmp_path / "dispatcher.log"))
    monkeypatch.setattr(fd, "APPROVALS_DIR", str(approvals))
    monkeypatch.setattr(fd, "DISARM_PATH", str(tmp_path / "DISARM"))
    monkeypatch.setattr(fd, "STOP_PATH", str(tmp_path / "STOP"))
    monkeypatch.setattr(fd, "GOVERNOR_CLI", FAKE_GOVERNOR)
    monkeypatch.setattr(fd, "GOVERNOR_STATE", str(gov_state))
    monkeypatch.setattr(fd, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(pacing, "PACING_CONFIG_PATH", str(tmp_path / "pacing_config.json"))
    monkeypatch.setattr(fd, "LANGFUSE_ENV", str(tmp_path / "absent-langfuse.env"))
    monkeypatch.setattr(fd, "STAGGER", (0.0, 0.0))
    monkeypatch.setattr(spawner, "GOVERNOR_CLI", FAKE_GOVERNOR)
    monkeypatch.setattr(concurrency, "LUMEN_RUN_BIN", lumen_run)
    monkeypatch.setitem(router.LANE_REPO, "testlane", str(repo_dir))
    monkeypatch.setitem(router.LANE_REPO, "fleet-fires", str(repo_dir))
    router.load_manifest(force=True)

    # wq#718: circuit_breaker/runaway read their own RUNS_ROOT/DISARM_PATH independent
    # of fd's — without this a test run reads the box's REAL _runs history and (if the
    # real fleet's failure rate ever crosses threshold) could disarm the REAL
    # production dispatcher. DISARM_PATH must be the SAME file fd.DISARM_PATH uses so
    # a circuit-breaker trip is actually visible to the next kill_switch() check.
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    monkeypatch.setattr(circuit_breaker, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(circuit_breaker, "DISARM_PATH", str(tmp_path / "DISARM"))
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(self_heal, "WQ_BIN", WQ_BIN)
    monkeypatch.setattr(notify_sinks, "NTFY_BIN", fake_ntfy)
    monkeypatch.setattr(notify_sinks, "XFCE_NOTIFYD_DB", "")

    return {"tmp": tmp_path, "repo": str(repo_dir), "approvals": approvals,
            "units_state": units_state, "calls_log": calls_log, "ntfy_log": ntfy_log,
            "runs_root": runs_root,
            "gov_state": gov_state, "cfg_path": cfg_path, "wq_db": str(wq_db),
            "bin_dir": bin_dir, "lumen_run": lumen_run,
            "pacing_cfg_path": tmp_path / "pacing_config.json"}


# ---- helpers -------------------------------------------------------------------------

def add_ticket(title, lane="testlane", pri=1, model="claude-sonnet-5", effort="high",
               deps=None, gate=None):
    cmd = [WQ_BIN, "add", "--lane", lane, "--pri", str(pri), "--title", title]
    if model:
        cmd += ["--model", model, "--effort", effort]
    if deps:
        cmd += ["--deps", deps]
    if gate:
        cmd += ["--gate", gate]
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def log_events(denv, event=None, ticket=None):
    path = denv["tmp"] / "dispatcher.log"
    if not path.exists():
        return []
    evs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if event is not None:
        evs = [e for e in evs if e["event"] == event]
    if ticket is not None:
        evs = [e for e in evs if e.get("ticket") == ticket]
    return evs


def lumen_calls(denv):
    if not denv["calls_log"].exists():
        return []
    return [json.loads(l) for l in denv["calls_log"].read_text().splitlines() if l.strip()]


def ntfy_lines(denv):
    if not denv["ntfy_log"].exists():
        return []
    return denv["ntfy_log"].read_text().splitlines()


def task_rows(denv):
    c = sqlite3.connect(denv["wq_db"])
    try:
        return c.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    finally:
        c.close()


def db_exec(denv, sql, params=()):
    c = sqlite3.connect(denv["wq_db"])
    try:
        c.execute(sql, params)
        c.commit()
    finally:
        c.close()


def add_decision_gate_columns(denv):
    """Mirror the live DB's orchestrator decision-gate columns on the scratch schema."""
    for col in ("gate_type", "frank_response"):
        try:
            db_exec(denv, f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # already added


def write_config(denv, **overrides):
    cfg = json.loads(json.dumps(BASE_CONFIG))
    cfg.update({k: v for k, v in overrides.items() if k != "lanes"})
    if "lanes" in overrides:
        cfg["lanes"] = overrides["lanes"]
    denv["cfg_path"].write_text(json.dumps(cfg))
    return cfg


# ---- MUST 8: kill switch -------------------------------------------------------------

def test_disarm_at_start(denv):
    add_ticket("eligible but disarmed")
    (denv["tmp"] / "DISARM").write_text("")
    assert fd.run_pass() == 0
    evs = log_events(denv)
    assert len(evs) == 1 and evs[0]["event"] == "halt" and evs[0]["stage"] == "kill_switch"
    assert "DISPATCH_DISARM" in evs[0]["reason"]
    assert lumen_calls(denv) == []


def test_stop_file_equivalent(denv):
    add_ticket("eligible but stopped")
    (denv["tmp"] / "STOP").write_text("")
    assert fd.run_pass() == 0
    evs = log_events(denv)
    assert len(evs) == 1 and evs[0]["stage"] == "kill_switch"
    assert "STOP" in evs[0]["reason"]
    assert lumen_calls(denv) == []


def test_disarm_midrun(denv, monkeypatch):
    t1 = add_ticket("first spawns then disarms")
    t2 = add_ticket("second must never be evaluated")

    def spawn_and_disarm(tid, repo):
        (denv["tmp"] / "DISARM").write_text("")
        return f"UNIT=fake-{tid}"

    monkeypatch.setattr(spawner, "dispatch_one", spawn_and_disarm)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [t1]
    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["stage"] == "kill_switch"
    assert log_events(denv, ticket=t2) == []


# ---- single instance -----------------------------------------------------------------

def test_slock_busy_noop(denv):
    add_ticket("someone else is dispatching")
    p = subprocess.run([SLOCK_BIN, "acquire", fd.SLOCK_SCOPE, "--ttl", "5",
                        "--holder", "another-dispatcher"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert fd.run_pass() == 0
    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["reason"] == "lock_busy"
    assert log_events(denv, "pass_start") == []
    assert lumen_calls(denv) == []


# ---- config fails closed -------------------------------------------------------------

def test_invalid_config_fails_closed(denv):
    denv["cfg_path"].write_text("{not json")
    assert fd.run_pass() == 1
    errs = log_events(denv, "error")
    assert len(errs) == 1 and errs[0]["stage"] == "config"
    assert len(ntfy_lines(denv)) == 1
    assert lumen_calls(denv) == []


# ---- MUST 3: Frank-gate --------------------------------------------------------------

def test_frank_gate_title_variants(denv):
    for title in ("[FRANK GATE] rotate keys", "cleanup [manual] step",
                  "retune floors owner=frank", "[Frank Gate] mixed case",
                  "[MANUAL] caps", "prefix owner = frank spaced"):
        add_ticket(title)
    before = task_rows(denv)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip")
    assert len(skips) == 6
    assert {s["reason"] for s in skips} == {"frank_gated"}
    assert task_rows(denv) == before  # zero wq writes: never claimed, never edited
    assert lumen_calls(denv) == []


def test_pending_gate_type_skipped(denv):
    tid = add_ticket("decision-gated ticket")
    add_decision_gate_columns(denv)
    db_exec(denv, "UPDATE tasks SET gate_type='approve-plan' WHERE id=?", (tid,))
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "frank_gated"
    assert lumen_calls(denv) == []

    db_exec(denv, "UPDATE tasks SET frank_response='approved' WHERE id=?", (tid,))
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_wq_gate_unmet_skipped(denv):
    tid = add_ticket("gated on a missing file", gate=str(denv["tmp"] / "no-such-file"))
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "frank_gated"
    assert "gate" in skips[0]["detail"]
    assert lumen_calls(denv) == []

    (denv["tmp"] / "no-such-file").write_text("")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


# ---- MUST 4: fire-ticket approval ----------------------------------------------------

def test_fire_requires_approval(denv):
    tid = add_ticket("burning issue", lane="fleet-fires")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "fire_unapproved"
    assert len(ntfy_lines(denv)) == 1 and f"wq#{tid}" in ntfy_lines(denv)[0]
    assert lumen_calls(denv) == []

    # second pass same day: skipped again, but Frank is NOT pinged again (24h dedup)
    assert fd.run_pass() == 0
    assert len(log_events(denv, "skip", ticket=tid)) == 2
    assert len(ntfy_lines(denv)) == 1

    (denv["approvals"] / f"wq{tid}.approved").write_text("")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    # approval file is consent, not consumed by the dispatcher
    assert (denv["approvals"] / f"wq{tid}.approved").exists()


def test_fire_approval_not_disarmable(denv):
    lanes = json.loads(json.dumps(BASE_CONFIG["lanes"]))
    lanes["fleet-fires"]["requires_approval"] = False  # hostile config edit
    write_config(denv, lanes=lanes)
    assert fd.load_config(str(denv["cfg_path"]))["lanes"]["fleet-fires"]["requires_approval"] is True

    tid = add_ticket("still needs Frank", lane="fleet-fires")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "fire_unapproved"
    assert lumen_calls(denv) == []


# ---- MUST 2: deps --------------------------------------------------------------------

def test_deps_unmet_skipped(denv):
    dep = add_ticket("blocking dep", lane="auto")  # unscanned lane, real ticket
    tid = add_ticket("wants the dep", deps=str(dep))
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "deps_unmet"
    assert str(dep) in skips[0]["detail"]
    assert lumen_calls(denv) == []

    p = subprocess.run([WQ_BIN, "edit", str(dep), "--status", "done"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_missing_dep_fails_closed(denv):
    tid = add_ticket("dep ticket does not exist", deps="99999")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "deps_unmet"
    assert lumen_calls(denv) == []


def test_wq_claim_backstop(denv):
    """DESIGN §3 TOCTOU: even if every dispatcher gate were bypassed, `wq claim --id`
    is the authoritative guard — a second claim on a held ticket refuses."""
    tid = add_ticket("raced ticket")
    p1 = subprocess.run([WQ_BIN, "claim", "backstopa", "--id", str(tid)],
                        capture_output=True, text=True)
    assert p1.returncode == 0, p1.stdout + p1.stderr
    p2 = subprocess.run([WQ_BIN, "claim", "backstopb", "--id", str(tid)],
                        capture_output=True, text=True)
    assert p2.returncode != 0 or "REFUS" in (p2.stdout + p2.stderr).upper()
    row = [r for r in task_rows(denv)]
    c = sqlite3.connect(denv["wq_db"])
    holder = c.execute("SELECT claimed_by FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    c.close()
    assert holder and "backstopa" in holder and row


# ---- MUST 5: budget ------------------------------------------------------------------

def test_governor_deny_skips(denv, monkeypatch):
    tid = add_ticket("denied by governor")
    monkeypatch.setenv("FAKE_GOVERNOR_DENY", "always")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "governor_deny"
    assert lumen_calls(denv) == []


def test_tight_mode_pri_filter(denv):
    denv["gov_state"].write_text(json.dumps(TIGHT_GOV))
    tid = add_ticket("p2 ineligible under tight", pri=2)
    assert fd.run_pass() == 0
    assert len(log_events(denv, "tight_mode")) == 1
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "tight_pri"
    assert lumen_calls(denv) == []


def test_tight_mode_single_spawn(denv):
    denv["gov_state"].write_text(json.dumps(TIGHT_GOV))
    t1 = add_ticket("tight p1 a", pri=1)
    add_ticket("tight p1 b", pri=1)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [t1]
    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["stage"] == "spawn_cap"
    assert len(lumen_calls(denv)) == 1


def test_lane_cap(denv):
    lanes = {"testlane": {"enabled": True, "max_per_day": 1}}
    write_config(denv, lanes=lanes)
    t1 = add_ticket("first under the cap")
    t2 = add_ticket("second over the cap")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [t1]
    skips = log_events(denv, "skip", ticket=t2)
    assert len(skips) == 1 and skips[0]["reason"] == "lane_cap"
    assert len(lumen_calls(denv)) == 1


def test_lane_cap_resets_on_day_rollover(denv):
    lanes = {"testlane": {"enabled": True, "max_per_day": 1}}
    write_config(denv, lanes=lanes)
    (denv["tmp"] / "dstate.json").write_text(json.dumps(
        {"day": "2000-01-01", "lane_counts": {"testlane": 99}, "fire_ntfy": {}}))
    tid = add_ticket("new day, fresh cap")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    st = json.loads((denv["tmp"] / "dstate.json").read_text())
    assert st["lane_counts"] == {"testlane": 1} and st["day"] != "2000-01-01"


# ---- wq#716 AUTO-4: weekly-budget pacing ----------------------------------------------

def _resets_at(elapsed_fraction):
    now = datetime.now(timezone.utc)
    remaining_s = pacing.WEEK_SECONDS * (1 - elapsed_fraction)
    return (now + timedelta(seconds=remaining_s)).isoformat()


def test_pacing_skip_blocks_hot_pool_regardless_of_priority(denv):
    # mirrors the actual wq#716 incident shape: ~99% pace at 21% elapsed. percent is kept
    # under the *governor's own* fable floor (remaining >= 50%) so this exercises pacing,
    # not the governor's reactive deny (session/weekly_all stay healthy).
    gov = {"limits": {
        "session": {"percent": 10}, "weekly_all": {"percent": 10},
        "fable_weekly_scoped": {"percent": 45, "resets_at": _resets_at(0.3)},  # -> 150%
    }}
    denv["gov_state"].write_text(json.dumps(gov))
    tid = add_ticket("hot fable ticket", pri=0, model="claude-fable-5")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "pacing_skip"
    assert skips[0]["pacing_pool"] == "fable_weekly_scoped"
    assert lumen_calls(denv) == []
    assert len(ntfy_lines(denv)) == 1
    assert "pacing" in ntfy_lines(denv)[0]


def test_pacing_delay_lets_high_priority_through(denv):
    gov = {"limits": {
        "session": {"percent": 10}, "fable_weekly_scoped": {"percent": 10},
        "weekly_all": {"percent": 56, "resets_at": _resets_at(0.5)},  # -> 112%: delay, not skip
    }}
    denv["gov_state"].write_text(json.dumps(gov))
    hi = add_ticket("p1 rides through pacing delay", pri=1)
    lo = add_ticket("p2 delayed by pacing", pri=2)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [hi]
    skips = log_events(denv, "skip", ticket=lo)
    assert len(skips) == 1 and skips[0]["reason"] == "pacing_delay"


def test_pacing_exempts_frank_approved_fire_ticket(denv):
    gov = {"limits": {
        "session": {"percent": 10}, "weekly_all": {"percent": 10},
        "fable_weekly_scoped": {"percent": 45, "resets_at": _resets_at(0.3)},  # -> 150%
    }}
    denv["gov_state"].write_text(json.dumps(gov))
    tid = add_ticket("approved fire ticket needs fable", lane="fleet-fires", pri=0,
                     model="claude-fable-5")
    (denv["approvals"] / f"wq{tid}.approved").write_text("")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert log_events(denv, "skip", ticket=tid) == []


def test_pacing_ntfy_dedup_within_window(denv):
    gov = {"limits": {
        "session": {"percent": 10}, "weekly_all": {"percent": 10},
        "fable_weekly_scoped": {"percent": 45, "resets_at": _resets_at(0.3)},
    }}
    denv["gov_state"].write_text(json.dumps(gov))
    add_ticket("hot fable ticket 1", pri=0, model="claude-fable-5")
    assert fd.run_pass() == 0
    assert len(ntfy_lines(denv)) == 1
    add_ticket("hot fable ticket 2", pri=0, model="claude-fable-5")
    assert fd.run_pass() == 0
    assert len(ntfy_lines(denv)) == 1  # deduped: same pool, still within ntfy_dedup_hours


def test_pacing_config_invalid_falls_back_conservative(denv):
    denv["pacing_cfg_path"].write_text("{not json")
    tid = add_ticket("unaffected by a broken pacing config")
    assert fd.run_pass() == 0
    infos = [e for e in log_events(denv, "info") if e.get("stage") == "pacing"]
    assert len(infos) == 1 and infos[0]["reason"] == "pacing_config_invalid"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_pacing_policy_off_disables_throttle(denv):
    denv["pacing_cfg_path"].write_text(json.dumps({**pacing.DEFAULT_CONFIG, "policy": "off"}))
    gov = {"limits": {
        "session": {"percent": 10}, "weekly_all": {"percent": 10},
        "fable_weekly_scoped": {"percent": 45, "resets_at": _resets_at(0.3)},
    }}
    denv["gov_state"].write_text(json.dumps(gov))
    tid = add_ticket("hot pool but pacing is off", pri=0, model="claude-fable-5")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


# ---- MUST 6: concurrency -------------------------------------------------------------

def test_ceiling_respected(denv):
    add_ticket("no slot for me")
    denv["units_state"].write_text(json.dumps(
        ["fworker-wq901", "fworker-wq902", "fworker-wq903"]))
    assert fd.run_pass() == 0
    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["reason"] == "no_slots"
    assert log_events(denv, "spawn") == []
    assert lumen_calls(denv) == []

    denv["units_state"].write_text(json.dumps(["fworker-wq901", "fworker-wq902"]))
    assert fd.run_pass() == 0
    assert len(log_events(denv, "spawn")) == 1


def test_local_excluded(denv):
    local = add_ticket("local rung worker", lane="auto", model="deepseek-v4-flash",
                       effort="low")
    tid = add_ticket("cloud candidate")
    denv["units_state"].write_text(json.dumps(
        [f"fworker-wq{local}", "fworker-wq901", "fworker-wq902"]))
    assert fd.run_pass() == 0  # 3 live units but only 2 cloud -> one slot free
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_lumen_run_unreachable_is_full(denv, monkeypatch):
    add_ticket("cannot count, cannot spawn")
    monkeypatch.setattr(concurrency, "LUMEN_RUN_BIN", str(denv["tmp"] / "nonexistent"))
    assert fd.run_pass() == 0
    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["reason"] == "lumen_run_unreachable"
    assert log_events(denv, "spawn") == []


def test_running_unit_skips_ticket(denv):
    tid = add_ticket("already has a worker")
    denv["units_state"].write_text(json.dumps([f"fworker-wq{tid}"]))
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "already_running"
    assert lumen_calls(denv) == []


# ---- MUST 7: router routing ----------------------------------------------------------

def test_repo_from_lane(denv):
    tid = add_ticket("routed through the lane map")
    assert fd.run_pass() == 0
    calls = lumen_calls(denv)
    assert len(calls) == 1
    args = calls[0]["args"]
    assert args[args.index("--repo") + 1] == denv["repo"]
    assert f"fworker-wq{tid}" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-5"


def test_unmapped_lane_skipped(denv):
    lanes = {"ghostlane": {"enabled": True, "max_per_day": 5}}
    write_config(denv, lanes=lanes)
    tid = add_ticket("no repo mapping", lane="ghostlane")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "lane_unmapped"
    assert lumen_calls(denv) == []  # never guessed /home/lumen or anything else


# ---- MUST 9: logging + telemetry -----------------------------------------------------

def test_every_decision_logged(denv):
    gated = add_ticket("[FRANK GATE] not ours")
    dep = add_ticket("open dep", lane="auto")
    blocked = add_ticket("dep-blocked", deps=str(dep))
    ok = add_ticket("clean spawn")
    assert fd.run_pass() == 0
    for tid in (gated, blocked, ok):
        evs = log_events(denv, ticket=tid)
        assert len(evs) == 1, f"wq#{tid}: expected exactly one decision, got {evs}"
        assert {"ts", "pass_id", "event", "ticket", "lane"} <= set(evs[0])
    assert len(log_events(denv, "pass_start")) == 1
    assert len(log_events(denv, "pass_end")) == 1
    assert log_events(denv, "pass_end")[0]["spawned"] == 1


def test_langfuse_best_effort(denv, monkeypatch):
    tid = add_ticket("telemetry must never block dispatch")

    def boom(pass_id, events):
        raise RuntimeError("langfuse down")

    monkeypatch.setattr(fd, "LANGFUSE_EMIT", boom)
    assert fd.run_pass() == 0  # pass still succeeds
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    errs = log_events(denv, "error")
    assert len(errs) == 1 and errs[0]["reason"] == "langfuse_emit_failed"


def test_langfuse_missing_keys_degrades_to_log_only(denv):
    add_ticket("no langfuse keys minted yet")
    assert fd.run_pass() == 0
    infos = log_events(denv, "info")
    assert len(infos) == 1 and infos[0]["reason"] == "langfuse_disabled"


def test_ntfy_on_spawn_failure(denv, monkeypatch):
    tid = add_ticket("spawn will fail")
    monkeypatch.setattr(spawner, "dispatch_one", lambda t, r: None)
    assert fd.run_pass() == 0
    errs = log_events(denv, "error", ticket=tid)
    assert len(errs) == 1 and errs[0]["reason"] == "spawn_failed"
    assert any(f"wq#{tid}" in l for l in ntfy_lines(denv))


# ---- MUST 1 + 10: scheduling, staged deploy, dry run ---------------------------------

def test_units_staged_disabled():
    sysd = os.path.join(DISPATCHER_DIR, "systemd")
    service = open(os.path.join(sysd, "lumen-foreman-dispatch.service")).read()
    timer = open(os.path.join(sysd, "lumen-foreman-dispatch.timer")).read()
    assert "Type=oneshot" in service
    assert "run --once" in service
    import re as _re  # a comment may mention [Install]; only a section header counts
    assert not _re.search(r"^\[Install\]", service, _re.M)  # timer-start only
    assert "OnUnitActiveSec=20min" in timer
    assert "Persistent=false" in timer
    assert "OnBootSec" in timer


def test_shipped_config_all_lanes_disabled():
    cfg = fd.load_config(os.path.join(DISPATCHER_DIR, "config.json"))
    assert cfg["lanes"], "shipped config must carry the lane allowlist"
    assert all(p["enabled"] is False for p in cfg["lanes"].values())
    assert cfg["lanes"]["fleet-fires"]["requires_approval"] is True


def test_once_pass_exits(denv):
    """The CLI entry the systemd unit calls: one pass, wired purely by env seams,
    terminates cleanly with no timer."""
    tmp = denv["tmp"]
    cfg = tmp / "subproc_config.json"
    cfg.write_text(json.dumps({"max_spawns_per_pass": 3, "cloud_ceiling": 3,
                               "tight_mode": {"max_spawns_per_pass": 1, "min_pri": 1},
                               "lanes": {}}))
    log = tmp / "subproc_dispatcher.log"
    env = dict(os.environ)
    env.update({
        "FOREMAN_ROOT": str(tmp),
        "FOREMAN_DISPATCHER_CONFIG": str(cfg),
        "FOREMAN_DISPATCHER_STATE": str(tmp / "subproc_state.json"),
        "FOREMAN_DISPATCHER_LOG": str(log),
        "FOREMAN_APPROVALS_DIR": str(denv["approvals"]),
        "FOREMAN_DISPATCH_DISARM": str(tmp / "DISARM"),
        "FOREMAN_STOP_FILE": str(tmp / "STOP"),
        "FOREMAN_SLOCK_BIN": SLOCK_BIN,
        "FOREMAN_NTFY_BIN": str(denv["bin_dir"] / "fake-ntfy"),
        "FOREMAN_LUMEN_RUN_BIN": denv["lumen_run"],
        "FOREMAN_GOVERNOR_CLI": FAKE_GOVERNOR,
        "FOREMAN_STATE_JSON": str(denv["gov_state"]),
        "FOREMAN_LANGFUSE_ENV": str(tmp / "absent-langfuse.env"),
    })
    p = subprocess.run([sys.executable,
                        os.path.join(DISPATCHER_DIR, "foreman_dispatcher.py"),
                        "run", "--once"],
                       capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    evs = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    assert [e["event"] for e in evs if e["event"] in ("pass_start", "pass_end")] == \
        ["pass_start", "pass_end"]


def test_dry_run_no_side_effects(denv):
    tid = add_ticket("dry run only looks")
    before = task_rows(denv)
    assert fd.run_pass(dry_run=True) == 0
    would = log_events(denv, "would_spawn")
    assert [e["ticket"] for e in would] == [tid]
    assert lumen_calls(denv) == []            # spawn stub untouched
    assert not (denv["tmp"] / "dstate.json").exists()  # state.json untouched
    assert task_rows(denv) == before          # DB untouched
    assert ntfy_lines(denv) == []


# ---- wq#718 AUTO-6: circuit breaker + runaway wiring ----------------------------------

def write_attempt(denv, tid, n, classification="KILLED", finished_at=None):
    d = denv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"attempt-{n}.json").write_text(json.dumps(
        {"ticket": tid, "attempt": n, "classification": classification,
         "finished_at": finished_at if finished_at is not None else time.time()}))


def test_circuit_breaker_trips_disarms_and_halts_before_config_load(denv, monkeypatch):
    monkeypatch.setattr(circuit_breaker, "WINDOW_MINUTES", 90)
    monkeypatch.setattr(circuit_breaker, "MIN_SAMPLES", 5)
    monkeypatch.setattr(circuit_breaker, "FAILURE_RATE_THRESHOLD", 0.75)
    for i in range(5):
        write_attempt(denv, 900 + i, 1, "KILLED")
    add_ticket("eligible but the breaker trips first")
    denv["cfg_path"].write_text("{not json")  # a broken config must not suppress the trip

    assert fd.run_pass() == 0

    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["stage"] == "circuit_breaker"
    assert (denv["tmp"] / "DISARM").exists()
    assert lumen_calls(denv) == []
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and "-p urgent" in lines[0]
    # the DISARM this pass wrote is the SAME file the dispatcher's own kill_switch()
    # reads: the very next pass halts at stage 0, before circuit_breaker even runs again
    assert fd.run_pass() == 0
    log2 = [json.loads(l) for l in (denv["tmp"] / "dispatcher.log").read_text().splitlines()
           if l.strip()]
    assert log2[-1]["event"] == "halt" and log2[-1]["stage"] == "kill_switch"


def test_circuit_breaker_dry_run_reports_without_disarming(denv, monkeypatch):
    monkeypatch.setattr(circuit_breaker, "WINDOW_MINUTES", 90)
    monkeypatch.setattr(circuit_breaker, "MIN_SAMPLES", 5)
    monkeypatch.setattr(circuit_breaker, "FAILURE_RATE_THRESHOLD", 0.75)
    for i in range(5):
        write_attempt(denv, 900 + i, 1, "KILLED")

    assert fd.run_pass(dry_run=True) == 0

    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["stage"] == "circuit_breaker"
    assert not (denv["tmp"] / "DISARM").exists()
    assert ntfy_lines(denv) == []


def test_circuit_breaker_below_threshold_pass_proceeds_normally(denv, monkeypatch):
    monkeypatch.setattr(circuit_breaker, "WINDOW_MINUTES", 90)
    monkeypatch.setattr(circuit_breaker, "MIN_SAMPLES", 5)
    monkeypatch.setattr(circuit_breaker, "FAILURE_RATE_THRESHOLD", 0.75)
    for i in range(5):
        write_attempt(denv, 900 + i, 1, "SUCCESS")
    tid = add_ticket("healthy fleet, this should spawn")

    assert fd.run_pass() == 0

    assert log_events(denv, "halt", ) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert not (denv["tmp"] / "DISARM").exists()


def test_runaway_stuck_ticket_skipped_and_frank_notified(denv):
    tid = add_ticket("keeps getting attempted")
    for n in range(1, 6):
        write_attempt(denv, tid, n)

    assert fd.run_pass() == 0

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["stage"] == "runaway"
    assert skips[0]["reason"] == "runaway_stuck"
    assert lumen_calls(denv) == []
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and f"wq#{tid}" in lines[0]

    # second pass same day: still skipped, but not re-pinged (24h dedup)
    assert fd.run_pass() == 0
    assert len(log_events(denv, "skip", ticket=tid)) == 2
    assert len(ntfy_lines(denv)) == 1


def test_runaway_below_threshold_not_skipped(denv):
    tid = add_ticket("only attempted a couple times")
    for n in range(1, 3):
        write_attempt(denv, tid, n)

    assert fd.run_pass() == 0

    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert log_events(denv, "skip", ticket=tid) == []
