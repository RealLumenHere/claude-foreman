"""foreman_dispatcher.py — safety-gate unit tests (wq#715 AUTO-3, DESIGN.md §11).

Zero quota, zero live services: the real `wq`/`slock` binaries run against a scratch
WQ_DB sqlite file; `lumen-run`, governor, and ntfy are the fake shims the harness tests
already use (harness/tests/fake_*.py); Langfuse is a nonexistent env file (log-only
degradation). Adversarial posture per DESIGN §11: every gate test asserts the negative
too — the lumen-run call log records zero spawns, not just a "skip" line.
"""
import json
import os
import signal
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
import burn_rate  # noqa: E402
import pacing  # noqa: E402
import circuit_breaker  # noqa: E402
import local_capacity  # noqa: E402
import runaway  # noqa: E402
import spawn_loop_guard  # noqa: E402
import self_heal  # noqa: E402
import notify_sinks  # noqa: E402
import psi_pressure  # noqa: E402
import repo_sweep  # noqa: E402
import harness_preflight  # noqa: E402
import capability_lint  # noqa: E402

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
    monkeypatch.setattr(fd, "_terminating", False)  # restored after tests that SIGTERM
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
    wq_db = tmp_path / "scratch_wq.db"

    # config.json is read from HEAD of its own repo (wq#772), never the working tree —
    # tmp_path is that repo; config commits go through commit_config()/write_config().
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "test"], check=True)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(BASE_CONFIG))
    commit_config(tmp_path, "initial test config")

    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_LUMEN_RUN_STATE", str(units_state))
    monkeypatch.setenv("FAKE_LUMEN_RUN_LOG", str(calls_log))
    monkeypatch.setenv("WQ_DB", str(wq_db))
    monkeypatch.setenv("WQ_AGENT", "test-dispatcher")
    # wq#1438: production defaults stage 3.7 to `apply-dir` (it gates and auto-tags).
    # Every test that predates slice C asserts a decision made WITHOUT that gate, so
    # the fixture pins slice B's mode and the slice-C tests below opt back in
    # explicitly — a gate nobody asked about must not silently rewrite 200 fixtures'
    # expectations. `test_capability_lint_mode_defaults_to_apply_dir` is what holds
    # the real default honest.
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "warn")
    monkeypatch.delenv("FAKE_GOVERNOR_DENY", raising=False)
    # wq#844 PSI check reads the real host's /proc/pressure/memory by default — point it
    # at a nonexistent path so these tests aren't at the mercy of the box's live memory
    # pressure (fail-open: psi_pressure._parse_psi() -> {} -> verdict "ok").
    monkeypatch.setattr(psi_pressure, "PSI_PATH", str(tmp_path / "no-such-psi-file"))

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
    # wq#1994: without this every test in this file shares (and leaks into) the REAL
    # production spawn_reserve.db, since spawner.py defaults SPAWN_RESERVE_DB to
    # dispatcher/runtime/spawn_reserve.db when FOREMAN_SPAWN_RESERVE_DB is unset.
    # Isolated per test exactly like test_spawner.py's spawner_env fixture (wq#1815).
    monkeypatch.setattr(spawner, "SPAWN_RESERVE_DB", str(tmp_path / "spawn_reserve.db"))
    # wq#2003, same class as wq#1994: burn_rate reads measured rates from
    # dispatcher/observed_burn.json in whatever checkout the suite runs from (wq#1722
    # writer). Pin it to a missing file so every ceiling asserted here is computed
    # from burn_rate.py's documented defaults, never from production measurements.
    monkeypatch.setattr(burn_rate, "OBSERVED_PATH", str(tmp_path / "no-observed-burn.json"))
    monkeypatch.setattr(concurrency, "LUMEN_RUN_BIN", lumen_run)
    # wq#2130: the local pause + backend cooldown files live under dispatcher/runtime/ —
    # keep every test off the production copies.
    monkeypatch.setattr(local_capacity, "LOCAL_PAUSE_PATH", str(tmp_path / "local_pause.json"))
    monkeypatch.setattr(local_capacity, "COOLDOWN_PATH", str(tmp_path / "backend_cooldown.json"))
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
    monkeypatch.setattr(notify_sinks, "DESKTOP_SSH_HOST", "")

    # wq#1703: the spawn-loop guard files an aq row. Point it at a fake binary — a unit
    # test must never open a row on the live alert queue.
    aq_log = tmp_path / "aq.log"
    fake_aq = _script(bin_dir / "fake-aq", f"#!/usr/bin/env bash\necho \"$@\" >> {aq_log}\n")
    monkeypatch.setattr(spawn_loop_guard, "AQ_BIN", fake_aq)

    return {"tmp": tmp_path, "repo": str(repo_dir), "approvals": approvals,
            "aq_log": aq_log,
            "units_state": units_state, "calls_log": calls_log, "ntfy_log": ntfy_log,
            "runs_root": runs_root,
            "gov_state": gov_state, "cfg_path": cfg_path, "wq_db": str(wq_db),
            "bin_dir": bin_dir, "lumen_run": lumen_run,
            "pacing_cfg_path": tmp_path / "pacing_config.json"}


# ---- helpers -------------------------------------------------------------------------

def add_ticket(title, lane="testlane", pri=1, model="claude-sonnet-5", effort="high",
               deps=None, gate=None, detail=None, archetype=None):
    # wq#912 lane allowlist checks against the LIVE dispatcher/config.json, not this
    # test's scratch WQ_DB — these synthetic lanes (testlane, ghostlane, ...) exercise
    # dispatcher logic only, never real dispatcher visibility, so --force bypasses it
    # (same fix wq#1004 applied to test_router.py/test_wq_reap.py for the same reason).
    # --force also bypasses wq#1368's own archetype-routing-row guard, needed for the
    # wq#1358/#1359 repro (archetype='build', a lane name, has no routing row).
    cmd = [WQ_BIN, "add", "--lane", lane, "--pri", str(pri), "--title", title, "--force"]
    if model:
        cmd += ["--model", model, "--effort", effort]
    if deps:
        cmd += ["--deps", deps]
    if gate:
        cmd += ["--gate", gate]
    if detail:
        cmd += ["--detail", detail]
    if archetype:
        cmd += ["--archetype", archetype]
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


# wq#1261 (#13 item 1): a fleet-fires ticket needs a fire_probe (or the legacy aq-row
# reference) to pass the dispatcher's no-probe refusal gate — every fleet-fires fixture
# below that expects a spawn/skip decision UNRELATED to the probe itself carries this.
FIRE_PROBE_DETAIL = 'fire_probe: {"cmd": "true", "clear_when_exit": 0}'


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


def add_needs_column(denv):
    """Same trick for wq#1331's `needs` column: a scratch DB built by plain `wq add`
    predates that migration, and `wq edit --needs` refuses outright without it — so a
    slice-C test would measure the missing column instead of the pre-skip."""
    try:
        db_exec(denv, "ALTER TABLE tasks ADD COLUMN needs TEXT")
    except sqlite3.OperationalError:
        pass  # already added


def needs_of(denv, tid):
    """The ticket's `needs` column, or "" when this scratch schema has none."""
    c = sqlite3.connect(denv["wq_db"])
    try:
        return (c.execute("SELECT needs FROM tasks WHERE id=?", (tid,)).fetchone()
                or [""])[0] or ""
    except sqlite3.OperationalError:
        return ""
    finally:
        c.close()


def notes_of(denv, tid):
    c = sqlite3.connect(denv["wq_db"])
    try:
        return (c.execute("SELECT notes FROM tasks WHERE id=?", (tid,)).fetchone()
                or [""])[0] or ""
    finally:
        c.close()


def commit_config(tmp_path, message):
    """Commit the working-tree config.json so load_config() (reads HEAD, wq#772) sees it.
    --allow-empty: some tests write back content identical to the current HEAD."""
    subprocess.run(["git", "-C", str(tmp_path), "add", "config.json"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "--allow-empty",
                    "-m", message], check=True)
    return subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def write_config(denv, **overrides):
    """Write + commit a new config.json — the daemon only sees committed config (wq#772)."""
    cfg = json.loads(json.dumps(BASE_CONFIG))
    cfg.update({k: v for k, v in overrides.items() if k != "lanes"})
    if "lanes" in overrides:
        cfg["lanes"] = overrides["lanes"]
    denv["cfg_path"].write_text(json.dumps(cfg))
    commit_config(denv["tmp"], "test config update")
    return cfg


def write_uncommitted_config(denv, **overrides):
    """Dirty the working-tree config.json WITHOUT committing — must never become live
    policy (wq#772). Returns the (unused-by-the-daemon) dict for assertion convenience."""
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


def _take_lock(holder):
    p = subprocess.run([SLOCK_BIN, "acquire", fd.SLOCK_SCOPE, "--ttl", "5",
                        "--holder", holder], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def _lock_holder():
    p = subprocess.run([SLOCK_BIN, "who", fd.SLOCK_SCOPE], capture_output=True, text=True)
    return p.stdout


def test_slock_stolen_from_dead_pass(denv):
    """wq#2080: lock left by a dead dispatcher pass must not wedge the next pass."""
    dead = subprocess.Popen(["true"])
    dead.wait()
    _take_lock(f"foreman-dispatcher:{dead.pid}")
    add_ticket("dead pass held the lock")
    assert fd.run_pass() == 0
    assert log_events(denv, "pass_start")
    assert not [h for h in log_events(denv, "halt") if h["reason"] == "lock_busy"]
    assert "foreman-dispatcher" not in _lock_holder()  # and it was released after


def test_slock_not_stolen_from_live_pass(denv, monkeypatch):
    """wq#2080: a live foreman_dispatcher holder is a real rival — never stolen."""
    _take_lock(f"foreman-dispatcher:{os.getpid()+1}")
    monkeypatch.setattr(fd, "_pass_pid_alive", lambda pid: True)
    assert fd.run_pass() == 0
    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["reason"] == "lock_busy"
    assert f"foreman-dispatcher:{os.getpid()+1}" in _lock_holder()


def test_pass_pid_alive_recycled_pid_is_dead():
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        assert not fd._pass_pid_alive(sleeper.pid)  # alive, but not a dispatcher
    finally:
        sleeper.kill()
        sleeper.wait()


def test_sigterm_releases_lock(denv, monkeypatch):
    """wq#2080: TimeoutStartSec's SIGTERM must unwind through the slock release."""
    add_ticket("term mid-pass")
    monkeypatch.setattr(fd, "_do_pass", lambda *a, **k: fd._term_to_exit(15, None))
    with pytest.raises(SystemExit):
        fd.run_pass()
    assert "foreman-dispatcher" not in _lock_holder()


def test_sigterm_inside_scan_wq_show_not_swallowed(denv, monkeypatch):
    """wq#2084: a real SIGTERM landing inside router.wq_show (whose not-found path is
    SystemExit, caught by the scan-stage handlers) must still abort the pass."""
    add_ticket("term mid-scan")

    def term_in_show(_tid):
        os.kill(os.getpid(), signal.SIGTERM)
        raise SystemExit("wq_show: not found")  # unreachable if the handler raised

    monkeypatch.setattr(router, "wq_show", term_in_show)
    old = signal.signal(signal.SIGTERM, fd._term_to_exit)
    try:
        with pytest.raises(SystemExit) as ei:
            fd.run_pass()
    finally:
        signal.signal(signal.SIGTERM, old)
    assert ei.value.code == 143
    assert not log_events(denv, "pass_end")
    assert "foreman-dispatcher" not in _lock_holder()


# ---- config fails closed -------------------------------------------------------------

def test_invalid_config_fails_closed(denv):
    denv["cfg_path"].write_text("{not json")
    commit_config(denv["tmp"], "break it")
    assert fd.run_pass() == 1
    errs = log_events(denv, "error")
    assert len(errs) == 1 and errs[0]["stage"] == "config"
    assert len(ntfy_lines(denv)) == 1
    assert lumen_calls(denv) == []


# ---- wq#772: config read from HEAD, never the working tree ---------------------------

def test_working_tree_config_edit_ignored(denv):
    """An uncommitted edit must never become live policy — no PR, no git record."""
    write_uncommitted_config(denv, max_spawns_per_pass=99)
    cfg, _warnings, sha = fd.load_config(str(denv["cfg_path"]))
    assert cfg["max_spawns_per_pass"] == BASE_CONFIG["max_spawns_per_pass"]
    want_sha = subprocess.run(["git", "-C", str(denv["tmp"]), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    assert sha == want_sha


def test_committed_config_change_picked_up(denv):
    write_config(denv, max_spawns_per_pass=7)
    cfg, _warnings, sha = fd.load_config(str(denv["cfg_path"]))
    assert cfg["max_spawns_per_pass"] == 7
    want_sha = subprocess.run(["git", "-C", str(denv["tmp"]), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    assert sha == want_sha


def test_pass_start_logs_config_source_and_sha(denv):
    assert fd.run_pass() == 0
    starts = log_events(denv, "pass_start")
    assert len(starts) == 1
    want_sha = subprocess.run(["git", "-C", str(denv["tmp"]), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    assert starts[0]["config_sha"] == want_sha
    assert starts[0]["config_path"] == str(denv["cfg_path"])


def test_config_missing_from_head_fails_closed(denv):
    subprocess.run(["git", "-C", str(denv["tmp"]), "rm", "-q", "config.json"], check=True)
    subprocess.run(["git", "-C", str(denv["tmp"]), "commit", "-q", "-m", "remove config"],
                   check=True)
    assert fd.run_pass() == 1
    errs = log_events(denv, "error")
    assert len(errs) == 1 and errs[0]["stage"] == "config"
    assert lumen_calls(denv) == []


def test_config_path_outside_any_repo_fails_closed(tmp_path_factory, denv, monkeypatch):
    stray = tmp_path_factory.mktemp("no-git-here") / "config.json"
    stray.write_text(json.dumps(BASE_CONFIG))
    monkeypatch.setattr(fd, "CONFIG_PATH", str(stray))
    assert fd.run_pass() == 1
    errs = log_events(denv, "error")
    assert len(errs) == 1 and errs[0]["stage"] == "config"
    assert lumen_calls(denv) == []


# ---- wq#428: epic/tracker parents are never dispatched --------------------------------

def test_nodispatch_title_variants(denv):
    for title in ("[EPIC][DO NOT CLAIM] LPP multiplayer", "[epic] lowercase rollup",
                  "[Do Not Claim] mixed case", "[DO-NOT-CLAIM] hyphenated",
                  "tracker [DONOTCLAIM] jammed"):
        add_ticket(title)
    before = task_rows(denv)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip")
    assert len(skips) == 5
    assert {s["reason"] for s in skips} == {"nodispatch_marker"}
    assert task_rows(denv) == before  # zero wq writes: never claimed, never edited
    assert lumen_calls(denv) == []


def test_nodispatch_marker_needs_brackets(denv):
    # "epic" as an ordinary adjective is real work, not a rollup marker
    tid = add_ticket("epic refactor of the do not claim helper")
    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=tid) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


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
    tid = add_ticket("burning issue", lane="fleet-fires", detail=FIRE_PROBE_DETAIL)
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


def test_fire_approval_config_honored(denv):
    """MUST-4 hardcode REMOVED 2026-08-23 per Frank directive: config.json's
    requires_approval is now honored. When fleet-fires has requires_approval:false,
    tickets dispatch WITHOUT an approval file (the old hardcode silently forced
    requires_approval:true regardless of config)."""
    lanes = json.loads(json.dumps(BASE_CONFIG["lanes"]))
    lanes["fleet-fires"]["requires_approval"] = False
    write_config(denv, lanes=lanes)
    cfg, _warnings, _sha = fd.load_config(str(denv["cfg_path"]))
    assert cfg["lanes"]["fleet-fires"]["requires_approval"] is False

    tid = add_ticket("fire ticket dispatches without approval", lane="fleet-fires",
                     detail=FIRE_PROBE_DETAIL)
    assert fd.run_pass() == 0
    spawns = log_events(denv, "spawn", ticket=tid)
    # With requires_approval:false, the ticket should spawn (not be skipped for approval)
    assert len(spawns) == 1 or len(log_events(denv, "would_spawn", ticket=tid)) == 1


def test_fire_approval_when_enabled(denv):
    """When requires_approval:true, fleet-fires tickets still need an approval file."""
    lanes = json.loads(json.dumps(BASE_CONFIG["lanes"]))
    lanes["fleet-fires"]["requires_approval"] = True
    write_config(denv, lanes=lanes)

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


# ---- wq#1461: hold (deps-blocked double-build guard) ---------------------------------
# Third instance of the class (wq#1410 a1/a2, wq#1409 09-07 09:37, wq#1411 orch vs
# fworker 09-07 10:49): an orchestrator directed to build a ticket whose deps are unmet
# cannot `claim --id` it (wq#582, no --force) and had no other way to record intent, so
# the moment the dep merged the dispatcher spawned an fworker on the SAME ticket. `wq
# hold` fixes that; these two tests are the ticket's own AC5 ("held ticket is not
# spawned, expired hold is").

def test_held_ticket_not_spawned(denv):
    tid = add_ticket("orch is mid-build on this one")
    p = subprocess.run([WQ_BIN, "hold", str(tid), "--by", "orch-1", "--ttl", "60"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "held"
    assert "orch-1" in skips[0]["detail"]
    assert lumen_calls(denv) == []


def test_expired_hold_is_spawned(denv):
    tid = add_ticket("hold expired before this pass")
    p = subprocess.run([WQ_BIN, "hold", str(tid), "--by", "orch-1", "--ttl", "60"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    # backdate hold_expires the same way test fixtures elsewhere backdate lease_expires —
    # direct db_exec, since `wq hold` has no --ttl<=0 escape hatch (by design: TTLs are
    # always future-facing at the CLI, matching claim's lease_from()).
    db_exec(denv, "UPDATE tasks SET hold_expires=? WHERE id=?",
           ("2000-01-01 00:00:00", tid))
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert not [e for e in log_events(denv, "skip", ticket=tid) if e["reason"] == "held"]


def test_ignore_holds_rollback(denv, monkeypatch):
    """Rollback: FOREMAN_IGNORE_HOLDS=1 makes the dispatcher blind to holds entirely."""
    tid = add_ticket("held but ignored")
    p = subprocess.run([WQ_BIN, "hold", str(tid), "--by", "orch-1", "--ttl", "60"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    monkeypatch.setenv("FOREMAN_IGNORE_HOLDS", "1")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


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


def test_bad_archetype_skips_ticket_without_crashing_pass(denv):
    """wq#1368: wq#1358/#1359 carried archetype='build' (a LANE name, not an archetype)
    straight onto a ticket. router.assign -> select -> candidates -> manifest.chain()
    raises ManifestError('no routing row for archetype build') for that shape, and it
    used to propagate uncaught out of candidate_model and out of _do_pass's per-ticket
    loop entirely — one bad-archetype ticket crashed the WHOLE pass (0 workers, 46 ready
    candidates sat idle live). A bad archetype must cost only its own ticket a skip;
    every other ready candidate in the same pass still gets a decision."""
    bad = add_ticket("wq1358-repro: lane name masquerading as archetype",
                      model=None, archetype="build")
    good = add_ticket("an ordinary candidate in the same pass")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=bad)
    assert len(skips) == 1 and skips[0]["reason"] == "bad_archetype"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [good]
    assert log_events(denv, "error") == []
    assert any("unroutable archetype" in line for line in ntfy_lines(denv))


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


def _burn_gov(session_pct, session_reset_h, weekly_pct, weekly_reset_h, severity=None):
    now = datetime.now(timezone.utc)
    s = {"percent": session_pct,
         "resets_at": (now + timedelta(hours=session_reset_h)).isoformat()}
    if severity:
        s["severity"] = severity
    return {"limits": {
        "session": s,
        "weekly_all": {"percent": weekly_pct,
                       "resets_at": (now + timedelta(hours=weekly_reset_h)).isoformat()},
        "fable_weekly_scoped": {"percent": 10}}}


def test_burn_rate_cap_replaces_binary_tight_clamp(denv):
    """wq#1669/wq#1699: governor pressure tight + usable percent/resets_at -> the
    burn-rate controller owns the ceiling (scaled, no tight_pri filter), not
    tight_mode's binary 1. session 55% used with 30min to reset = an expiring budget:
    45% remaining less Frank's 30% session reserve (burn_rate.FRANK_RESERVE_PCT) is
    15%/0.5h = 30%/h over 1.5%/worker-h -> 20 concurrent; weekly_all (90% remaining
    less its 15% reserve, over 48h) is the tighter pool at 4.5, clamped to
    cloud_ceiling 3. wq#2004: this test was written at fee4c54 and read 70% here, which
    c7cd075's reserve turned into zero spendable session budget -> ceiling 1."""
    denv["gov_state"].write_text(json.dumps(_burn_gov(55, 0.5, 10, 48, severity="warning")))
    t1 = add_ticket("burn p1", pri=1)
    t2 = add_ticket("burn p2 spawns too", pri=2)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [t1, t2]
    assert len(log_events(denv, "burn_rate")) == 1
    assert log_events(denv, "tight_mode") == []
    assert [e for e in log_events(denv, "skip") if e["reason"] == "tight_pri"] == []
    # the raw severity labels are surfaced once per pass
    adv = [e for e in log_events(denv, "info") if e.get("reason") == "api_severity"]
    assert len(adv) == 1 and "session=warning" in adv[0]["detail"]


def test_burn_rate_cap_shrinks_when_burn_outruns_budget(denv):
    # weekly 71% used with 100h to the reset projects past the budget -> min_allowed 1
    # (pacing off: this test is about the burn-rate cap, not the wq#716 layer, which
    # also fires on these numbers)
    denv["pacing_cfg_path"].write_text(json.dumps(
        {"policy": "off", "min_elapsed_fraction": 0.02, "delay_min_pri": 1,
         "ntfy_dedup_hours": 6}))
    denv["gov_state"].write_text(json.dumps(_burn_gov(10, 2, 71, 100)))
    t1 = add_ticket("scarce p1 a", pri=1)
    add_ticket("scarce p1 b", pri=1)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [t1]
    halts = log_events(denv, "halt")
    # wq#1699: a concurrency ceiling, halted exactly like cloud_ceiling (no_slots)
    assert len(halts) == 1 and halts[0]["stage"] == "concurrency"
    assert halts[0]["reason"] == "no_slots" and "burn-rate" in halts[0]["detail"]
    assert len(lumen_calls(denv)) == 1


def test_burn_rate_no_resets_at_falls_back_to_binary_tight(denv):
    # TIGHT_GOV has no resets_at anywhere -> controller has no data -> old clamp
    denv["gov_state"].write_text(json.dumps(TIGHT_GOV))
    tid = add_ticket("fallback p2", pri=2)
    assert fd.run_pass() == 0
    assert len(log_events(denv, "tight_mode")) == 1
    assert log_events(denv, "burn_rate") == []
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "tight_pri"


def test_frank_active_row_drops_the_burn_rate_ceiling_dry_run(denv):
    """wq#1889 acceptance: a dry-run pass showing the burn-rate ceiling drop while an
    orch-* agent_sessions row is fresh. session 50% used, severity=warning forces
    governor 'tight' regardless of margin; weekly is given abundant near-term headroom
    so it never becomes the limiting pool — session alone drives the ceiling in both
    passes. Same config, same percents; only the fresh orch- row differs between them."""
    denv["gov_state"].write_text(json.dumps(_burn_gov(50, 4, 5, 1, severity="warning")))

    add_ticket("frank inactive baseline", pri=1)
    assert fd.run_pass(dry_run=True) == 0
    baseline = log_events(denv, "pass_end")[-1]
    assert baseline["frank_active"] is False
    baseline_ceiling = next(iter(baseline["burn_ceiling"].values()))
    assert baseline_ceiling == 3  # (100-50)-30=20% / 4h = 5%/h over 1.5 -> 3.3 -> 3

    con = sqlite3.connect(denv["wq_db"])
    fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    con.execute("INSERT INTO agent_sessions VALUES (?,?,?,?,?,?)",
                ("orch-wq1889-test", "orch-wq1889-test", "lumen", 1, fresh, fresh))
    con.commit()
    con.close()

    add_ticket("frank active", pri=1)
    assert fd.run_pass(dry_run=True) == 0
    active = log_events(denv, "pass_end")[-1]
    assert active["frank_active"] is True
    assert "orch-wq1889-test" in active["frank_active_detail"]
    active_ceiling = next(iter(active["burn_ceiling"].values()))
    assert active_ceiling == 1  # (100-50)-45=5% / 4h = 1.25%/h over 1.5 -> 0.83 -> min_allowed 1
    assert active_ceiling < baseline_ceiling


def _seed_running(units):
    with open(os.environ["FAKE_LUMEN_RUN_STATE"], "w") as f:
        json.dump(units, f)


def _zero_floors(denv, monkeypatch):
    """Frank's live governor/floors.json shape (2026-08-23 + wq#1669): every floor and
    the soft margin 0. router reads floors.json next to the governor CLI, so point the
    dispatcher at a private copy of the fake governor with that file beside it."""
    gdir = denv["tmp"] / "gov_zero"
    gdir.mkdir()
    cli = gdir / "fake_governor.py"
    cli.write_text(open(FAKE_GOVERNOR).read())
    (gdir / "floors.json").write_text(json.dumps({
        "session_min_remaining_pct": 0, "weekly_all_min_remaining_pct": 0,
        "fable_weekly_min_remaining_pct": 0, "soft_margin_pct": 0}))
    monkeypatch.setattr(fd, "GOVERNOR_CLI", str(cli))


def _sec5_gov(weekly_severity):
    now = datetime.now(timezone.utc)
    return {"limits": {
        "session": {"percent": 80, "severity": "warning",
                    "resets_at": (now + timedelta(minutes=40)).isoformat()},
        "weekly_all": {"percent": 83, "severity": weekly_severity,
                       "resets_at": (now + timedelta(hours=4)).isoformat()},
        "fable_weekly_scoped": {"percent": 10}}}


def test_sec5_zero_floors_warning_does_not_throttle(denv, monkeypatch):
    """Walkthrough sec 5 numbers (2026-09-10 05:30Z-07:01Z) on Frank's zeroed floors:
    severity=warning everywhere used to clamp every pass to 1 spawn, pri<=1. Warning is
    advisory on a zero-floor pool: no tight_mode, no burn_rate, p2 spawns to root cap."""
    _zero_floors(denv, monkeypatch)
    denv["gov_state"].write_text(json.dumps(_sec5_gov("warning")))
    ids = [add_ticket(f"sec5 p2 {n}", pri=2) for n in range(4)]
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == ids[:3]
    assert log_events(denv, "tight_mode") == [] and log_events(denv, "burn_rate") == []


def test_sec5_zero_floors_critical_engages_scaled_ceiling(denv, monkeypatch):
    """wq#1699 warning != critical: critical engages the controller even with floors
    zeroed, and the ceiling it hands back is budget-scaled rather than tight_mode's
    binary clamp — so p2 tickets are still candidates (no tight_pri filter), they just
    queue behind a narrow ceiling.

    wq#2004: on these replayed sec-5 numbers that ceiling is now 1, not cloud_ceiling.
    The test was written at fee4c54; c7cd075 then reserved a slice of each pool for
    Frank's own interactive use, and at 83% weekly only 2% of the week sits above the
    15% weekly reserve — 0.5%/h over 0.35%/worker-h = 1.4 -> 1. Session contributes
    nothing at all (80% used vs a 30% reserve). That is the reserve working as
    specified, not a regression: three concurrent workers on 2% of spendable weekly
    budget is exactly what it exists to stop. The controller opening the ceiling to
    cloud_ceiling is covered by test_burn_rate_cap_replaces_binary_tight_clamp."""
    _zero_floors(denv, monkeypatch)
    denv["gov_state"].write_text(json.dumps(_sec5_gov("critical")))
    ids = [add_ticket(f"sec5 crit p2 {n}", pri=2) for n in range(4)]
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == ids[:1]
    burn = log_events(denv, "burn_rate")
    assert len(burn) == 1 and "burn-rate ceiling 1" in burn[0]["reason"]
    assert log_events(denv, "tight_mode") == []
    # the discriminator against the binary clamp: p2 is never filtered out, it is
    # ceiling-bound, so the pass ends on the burn-rate concurrency gate.
    assert [e for e in log_events(denv, "skip") if e["reason"] == "tight_pri"] == []
    stops = log_events(denv, "halt")
    assert len(stops) == 1 and stops[0]["stage"] == "concurrency"
    assert stops[0]["reason"] == "no_slots" and "burn-rate" in stops[0]["detail"]


def test_burn_rate_fable_tight_never_halts_unflagged_pools(denv, monkeypatch):
    """wq#1699: a fable model can be tight from its own pool alone. Its ceiling must
    not halt the pass: with weekly_all scarce but unflagged (floors 0, no severity),
    sonnet is not throttled at all, so the fable skip must leave the sonnet spawn."""
    _zero_floors(denv, monkeypatch)
    denv["pacing_cfg_path"].write_text(json.dumps(
        {"policy": "off", "min_elapsed_fraction": 0.02, "delay_min_pri": 1,
         "ntfy_dedup_hours": 6}))
    now = datetime.now(timezone.utc)
    denv["gov_state"].write_text(json.dumps({"limits": {
        "session": {"percent": 10, "resets_at": (now + timedelta(hours=4)).isoformat()},
        "weekly_all": {"percent": 60, "resets_at": (now + timedelta(hours=100)).isoformat()},
        "fable_weekly_scoped": {"percent": 70, "severity": "critical",
                                "resets_at": (now + timedelta(hours=100)).isoformat()}}}))
    _seed_running(["fworker-wq999993"])
    fable = add_ticket("fable ticket, weekly-bound ceiling", pri=1, model="claude-fable-5")
    sonnet = add_ticket("sonnet on unflagged pools", pri=2)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=fable)
    assert len(skips) == 1 and skips[0]["reason"] == "burn_rate_cap"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [sonnet]
    assert [h for h in log_events(denv, "halt") if h["stage"] == "concurrency"] == []


def _age_ticket(denv, tid, days):
    c = sqlite3.connect(denv["wq_db"])
    old = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE tasks SET created_at=? WHERE id=?", (old, tid))
    c.commit()
    c.close()


def test_tight_pri_gates_on_epri_not_raw_pri(denv):
    """wq#1699: the scan sorts on the wq#661 aged effective priority, but tight_pri
    compared raw pri — an aged p2 at ep1 sorted first and was still refused. Binary
    fallback (no resets_at) so tight_pri is live."""
    denv["gov_state"].write_text(json.dumps(TIGHT_GOV))
    aged = add_ticket("aged p2 now ep1", pri=2)
    _age_ticket(denv, aged, 8)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [aged]
    assert [e for e in log_events(denv, "skip") if e["reason"] == "tight_pri"] == []


def test_psi_tight_pri_gates_on_epri(denv, monkeypatch):
    monkeypatch.setattr(psi_pressure, "check", lambda cfg=None: ("tight", "psi test"))
    aged = add_ticket("psi aged p2", pri=2)
    _age_ticket(denv, aged, 8)
    fresh = add_ticket("psi fresh p2", pri=2)
    write_config(denv, tight_mode={"max_spawns_per_pass": 2, "min_pri": 1})
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [aged]
    skips = log_events(denv, "skip", ticket=fresh)
    assert len(skips) == 1 and skips[0]["reason"] == "tight_pri"


def test_burn_rate_full_ceiling_with_zero_spawns_is_healthy(denv):
    """wq#1699: a pass that spawns nothing because the burn-rate ceiling is already
    occupied is the cloud_ceiling case, not a stall — dispatch_health must not page it."""
    import dispatch_health
    denv["pacing_cfg_path"].write_text(json.dumps(
        {"policy": "off", "min_elapsed_fraction": 0.02, "delay_min_pri": 1,
         "ntfy_dedup_hours": 6}))
    denv["gov_state"].write_text(json.dumps(_burn_gov(10, 2, 71, 100)))
    _seed_running(["fworker-wq999991"])
    add_ticket("budget ceiling already occupied", pri=1)
    assert fd.run_pass() == 0
    assert log_events(denv, "spawn") == []
    halts = log_events(denv, "halt")
    assert len(halts) == 1 and halts[0]["stage"] == "concurrency"
    events = [json.loads(l) for l in (denv["tmp"] / "dispatcher.log").read_text().splitlines()
              if l.strip()]
    verdicts = [v for _, _, v, _ in dispatch_health.classify_passes(events)]
    assert verdicts == ["healthy"]


def test_burn_rate_fable_pool_skips_fable_only(denv, monkeypatch):
    """wq#1699: fable_weekly_scoped critical and scarce narrows only the fable model's
    ceiling. The fable candidate is skipped (burn_rate_cap) and scanning continues so
    the sonnet candidate behind it still spawns."""
    now = datetime.now(timezone.utc)
    denv["gov_state"].write_text(json.dumps({"limits": {
        "session": {"percent": 10, "resets_at": (now + timedelta(hours=4)).isoformat()},
        "weekly_all": {"percent": 10, "resets_at": (now + timedelta(hours=20)).isoformat()},
        "fable_weekly_scoped": {"percent": 99, "severity": "critical",
                                "resets_at": (now + timedelta(hours=100)).isoformat()}}}))
    _zero_floors(denv, monkeypatch)
    denv["pacing_cfg_path"].write_text(json.dumps(  # isolate stage 9 from wq#716 pacing
        {"policy": "off", "min_elapsed_fraction": 0.02, "delay_min_pri": 1,
         "ntfy_dedup_hours": 6}))
    _seed_running(["fworker-wq999992"])
    fable = add_ticket("fable ticket, pool nearly spent", pri=1, model="claude-fable-5")
    sonnet = add_ticket("sonnet ticket behind it", pri=2)
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=fable)
    assert len(skips) == 1 and skips[0]["reason"] == "burn_rate_cap"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [sonnet]
    assert [h for h in log_events(denv, "halt") if h["stage"] == "concurrency"] == []


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
                     model="claude-fable-5", detail=FIRE_PROBE_DETAIL)
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


def test_stale_preflight_auto_reproves_and_spawns(denv, monkeypatch):
    """wq#1372: a stale stamp must not strand the lane — a clean re-prove writes a
    fresh stamp and the ticket dispatches in the same pass, no human re-run needed."""
    tid = add_ticket("stale stamp, clean reprove")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    calls = []

    def fake_preflight(repo):
        calls.append(repo)
        return harness_preflight.PreflightResult(repo, ok=True, reason="ok",
                                                  static_failures=[], denials=[])
    monkeypatch.setattr(harness_preflight, "preflight", fake_preflight)
    assert fd.run_pass() == 0
    assert calls == [denv["repo"]]
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert log_events(denv, "skip", ticket=tid) == []
    infos = [e for e in log_events(denv, "info") if e.get("reason") == "auto_reproved"]
    assert len(infos) == 1
    assert ntfy_lines(denv) == []


def test_stale_preflight_reprove_failure_alerts_instead_of_silent_skip(denv, monkeypatch):
    """A re-prove that still fails (denials survive the settings change) must alert
    Frank, not just log another silent skip like the old behavior did forever."""
    tid = add_ticket("stale stamp, reprove still denied")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    monkeypatch.setattr(
        harness_preflight, "preflight",
        lambda repo: harness_preflight.PreflightResult(
            repo, ok=False, reason="live proof found permission denials",
            static_failures=[], denials=["This command requires approval: foo"]))
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "reprove_failed"
    assert lumen_calls(denv) == []
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and "reprove failed" in lines[0] and "repo" in lines[0]


def test_stale_preflight_reprove_cooldown_avoids_repeat_live_proof(denv, monkeypatch):
    """A live re-prove spawns a real throwaway claude session — a persistently broken
    profile must not pay for that on every single dispatch pass."""
    t1 = add_ticket("first stale-stamp pass")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    calls = []

    def fake_preflight(repo):
        calls.append(repo)
        return harness_preflight.PreflightResult(
            repo, ok=False, reason="live proof found permission denials",
            static_failures=[], denials=["denied"])
    monkeypatch.setattr(harness_preflight, "preflight", fake_preflight)
    assert fd.run_pass() == 0
    assert len(calls) == 1
    assert len(ntfy_lines(denv)) == 1

    t2 = add_ticket("second pass, still within cooldown")
    assert fd.run_pass() == 0
    assert len(calls) == 1  # not re-run — cooldown still active
    assert len(ntfy_lines(denv)) == 1  # not re-alerted
    skips = log_events(denv, "skip", ticket=t2)
    assert len(skips) == 1 and skips[0]["reason"] == "no_fresh_stamp"
    assert lumen_calls(denv) == []
    assert log_events(denv, "spawn", ticket=t1) == []


def test_stale_preflight_live_proof_exception_bypasses_cooldown(denv, monkeypatch):
    """wq#1384: wq#1380 found that a raw exception out of harness_preflight.preflight()
    (TimeoutExpired on a hung live-proof session, FileNotFoundError if claude_bin isn't
    on PATH) used to propagate past handle_stale_preflight() and abort the whole pass —
    preflight_ntfy never got written, so the reprove cooldown never engaged and the next
    pass re-spawned the same doomed live-proof session forever. An exception must be
    treated identically to a clean ok=False: alert once, write the cooldown, skip."""
    t1 = add_ticket("first stale-stamp pass, preflight raises")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    slept = []
    monkeypatch.setattr(fd, "_sleep", slept.append)
    calls = []

    def raising_preflight(repo):
        calls.append(repo)
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=300)
    monkeypatch.setattr(harness_preflight, "preflight", raising_preflight)
    assert fd.run_pass() == 0
    # wq#1409 §2.2: a raise is a TRANSIENT error — the in-pass ladder runs every rung
    # (3 attempts, 0/30/120s) and only then pages, urgent, naming the exception.
    assert len(calls) == 3
    assert slept == [30, 120]
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and "-p urgent" in lines[0] and "TimeoutExpired" in lines[0]
    assert "3x" in lines[0]

    t2 = add_ticket("second pass, still within cooldown")
    assert fd.run_pass() == 0
    assert len(calls) == 3  # not re-run — cooldown still active despite the raise
    assert len(ntfy_lines(denv)) == 1  # not re-alerted
    skips = log_events(denv, "skip", ticket=t2)
    assert len(skips) == 1 and skips[0]["reason"] == "no_fresh_stamp"
    st = fd.load_state()
    assert st["preflight_ntfy"]["repo"]["attempts"] == 3
    assert st["preflight_ntfy"]["repo"]["paged"] is True
    assert lumen_calls(denv) == []
    assert log_events(denv, "spawn", ticket=t1) == []


def test_auto_lane_repo_sweep_ticket_dispatches_via_repo_hint(denv, monkeypatch):
    """wq#1094: 'auto' has no LANE_REPO entry (repo-sweeps span arbitrary repos), but a
    repo-sweep ticket carries its own target repo in its detail — the dispatcher must
    read that back and dispatch instead of skipping it forever as lane_unmapped."""
    write_config(denv, lanes={**BASE_CONFIG["lanes"],
                              "auto": {"enabled": True, "max_per_day": 10}})
    repo = denv["repo"]
    monkeypatch.setattr(repo_sweep, "managed_repos", lambda: {repo})
    short = os.path.basename(repo)
    title = f"[repo-sweep] {short}: BEHIND_ONLY"
    detail = (f"Automated sweep detected BEHIND_ONLY in {repo}.\n"
              f"Output: BEHIND_ONLY {repo} main ahead=0 behind=1")
    p = subprocess.run([WQ_BIN, "add", "--lane", "auto", "--pri", "1", "--title", title,
                       "--detail", detail, "--model", "claude-sonnet-5", "--effort", "high"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    tid = int(p.stdout.strip().split()[2])

    assert fd.run_pass() == 0
    assert log_events(denv, "skip", ticket=tid) == []
    calls = lumen_calls(denv)
    assert len(calls) == 1
    args = calls[0]["args"]
    assert args[args.index("--repo") + 1] == repo
    assert f"fworker-wq{tid}" in args


def test_auto_lane_non_sweep_ticket_still_unmapped(denv):
    """A plain 'auto'-lane ticket (not repo-sweep-shaped) must still refuse rather than
    guess — the repo hint only ever applies to tickets repo_sweep.py itself filed."""
    write_config(denv, lanes={**BASE_CONFIG["lanes"],
                              "auto": {"enabled": True, "max_per_day": 10}})
    tid = add_ticket("just an ordinary auto-lane ticket", lane="auto")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "lane_unmapped"
    assert lumen_calls(denv) == []


def test_lane_repo_mismatch_skipped_not_dispatched(denv):
    """wq#433 / ISSUE-LOG #56 class: the lane maps fine, but the TITLE names a different
    repo. Dispatching would hand the worker a tree holding none of the paths its ticket
    cites — and --add-dir grants the right tree READ access only, so the attempt cannot
    land there either. Skip with the reason instead of burning an attempt."""
    tid = add_ticket("[lpp-mp] M5d: cross-node link over Tailscale (lumen<->Thor)")
    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "repo_mismatch"
    assert "/home/lumen/pokemon" in skips[0]["detail"]
    assert lumen_calls(denv) == []


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


def test_shipped_config_loads_without_warnings():
    cfg, warnings, sha = fd.load_config(os.path.join(DISPATCHER_DIR, "config.json"))
    assert cfg["lanes"], "shipped config must carry the lane allowlist"
    assert warnings == [], f"shipped config has lane warnings: {warnings}"
    # MUST-4 hardcode removed 2026-08-23 per Frank directive: config.json's
    # requires_approval:false for fleet-fires is now honored as-is.
    assert cfg["lanes"]["fleet-fires"]["requires_approval"] is False
    assert sha


def test_once_pass_exits(denv):
    """The CLI entry the systemd unit calls: one pass, wired purely by env seams,
    terminates cleanly with no timer."""
    tmp = denv["tmp"]
    cfg = tmp / "subproc_config.json"
    cfg.write_text(json.dumps({"max_spawns_per_pass": 3, "cloud_ceiling": 3,
                               "tight_mode": {"max_spawns_per_pass": 1, "min_pri": 1},
                               "lanes": {}}))
    subprocess.run(["git", "-C", str(tmp), "add", "subproc_config.json"], check=True)
    subprocess.run(["git", "-C", str(tmp), "commit", "-q", "-m", "subproc config"], check=True)
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


# ---- wq#1415: harness-defect hold (SpawnRefused never writes attempt-N.json, so the
# runaway breaker above never sees it) --------------------------------------------------

def write_failure_json(denv, tid, triage="HARNESS_DEFECT", summary="CITED_PATH_UNREACHABLE"):
    d = denv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "failure.json").write_text(json.dumps(
        {"ticket": tid, "triage": triage, "summary": summary}))


def test_harness_defect_ticket_held_and_frank_notified(denv):
    tid = add_ticket("keeps hitting CITED_PATH_UNREACHABLE")
    write_failure_json(denv, tid)

    assert fd.run_pass() == 0

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["stage"] == "harness_defect"
    assert skips[0]["reason"] == "harness_defect_hold"
    assert lumen_calls(denv) == []
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and f"wq#{tid}" in lines[0]

    # second pass same day: still held, but not re-pinged (24h dedup) — this is the
    # "at most one event-driven pass worth of noise" the ticket asks for, even though
    # a refill kick can still trigger the pass itself every couple of minutes.
    assert fd.run_pass() == 0
    assert len(log_events(denv, "skip", ticket=tid)) == 2
    assert len(ntfy_lines(denv)) == 1


def test_harness_defect_clears_once_triage_changes(denv):
    """Once a later attempt gets past preflight (a fresh non-HARNESS_DEFECT failure.json,
    e.g. a real classified failure that DOES charge an attempt), the hold lifts on its
    own — no manual unblock step needed."""
    tid = add_ticket("defect got fixed, now fails for a real reason")
    write_failure_json(denv, tid, triage="GENUINE_BUG", summary="assertion error")

    assert fd.run_pass() == 0

    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert log_events(denv, "skip", ticket=tid) == []


# ---- wq#1703: spawn-loop guard (a worker that dies before writing EITHER artifact is
# invisible to both breakers above — ISSUE-LOG #283: wq#1543 spawned 31x in 3h) ---------

def aq_rows(denv):
    if not denv["aq_log"].exists():
        return []
    return denv["aq_log"].read_text().splitlines()


@pytest.fixture
def loop_guard(monkeypatch):
    """Trip on the 3rd pass instead of the 4th: the fixture's cloud_ceiling is 3, and
    this test is about the guard, not the concurrency ceiling.

    wq#2004 (ISSUE-LOG #415): these tests fire back-to-back run_pass() calls that
    production fires MINUTES apart (wq#1883 measured 4-5 min), and every pass in the
    ISSUE-LOG #283 shape targets the same (ticket, attempt) because attempt_count never
    moves. wq#1893's DEAD_OWNER_GRACE_S refuses to judge a reservation younger than 60s
    — its owner may simply not have Popen'd yet — so in test time the second pass reads
    every crashed worker's dangling reservation as "too young to reclaim" and refuses
    before Popen, and the guard never gets a second spawn to count. Zeroing the grace is
    how wq#1893's own suite models elapsed time (harness/tests/test_spawner.py:185-188,
    :205-207) and leaves the reclaim decision to the signal that actually matters here:
    reservation_owner_alive(), which crash_pass falsifies by clearing the unit list.
    The grace itself is covered by test_spawner.py's racing-duplicate test."""
    monkeypatch.setattr(spawn_loop_guard, "SPAWN_LOOP_MAX", 2)
    monkeypatch.setattr(spawner, "DEAD_OWNER_GRACE_S", 0)


def crash_pass(denv, dry_run=False):
    """One dispatch pass whose worker dies leaving nothing behind — the ISSUE-LOG #283
    shape. Clearing the fake unit list is what the crash does in production (the unit
    exits, so the next pass no longer skips `already_running`); no attempt-N.json and no
    failure.json are written, which is exactly why both older breakers stay blind."""
    assert fd.run_pass(dry_run=dry_run) == 0
    denv["units_state"].write_text("[]")


def test_repeated_spawns_with_no_finished_attempt_stop_and_open_an_aq_row(denv, loop_guard):
    tid = add_ticket("crashes before it can write attempt-N.json")

    for _ in range(2):
        crash_pass(denv)
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid, tid]
    assert log_events(denv, "skip", ticket=tid) == []

    crash_pass(denv)  # third pass: the guard refuses

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["stage"] == "spawn_loop"
    assert skips[0]["reason"] == "spawn_loop"
    assert "2 spawns since" in skips[0]["detail"]
    assert len(lumen_calls(denv)) == 2  # the refusal really cost nothing
    rows = aq_rows(denv)
    assert len(rows) == 1 and f"spawn-loop-wq{tid}" in rows[0]
    assert log_events(denv, "aq_row", ticket=tid)[0]["reason"] == "filed"
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and f"wq#{tid}" in lines[0]


def test_a_held_ticket_is_not_re_filed_or_re_pinged_every_pass(denv, loop_guard):
    """The loop this replaces fired ~10 passes/hour; one aq row and one ntfy per 24h."""
    tid = add_ticket("loops forever")
    for _ in range(4):
        crash_pass(denv)

    assert len(log_events(denv, "skip", ticket=tid)) == 2  # passes 3 and 4
    assert len(aq_rows(denv)) == 1
    assert len(ntfy_lines(denv)) == 1


def test_a_finished_attempt_lets_the_ticket_dispatch_again(denv, loop_guard):
    """Progress is the same signal the wq#718 breaker counts: a spawn that produced an
    attempt-N.json is an honest retry, not a loop."""
    tid = add_ticket("slow but making progress")
    for _ in range(2):
        crash_pass(denv)
    write_attempt(denv, tid, 1)

    crash_pass(denv)

    assert len(log_events(denv, "spawn", ticket=tid)) == 3
    assert log_events(denv, "skip", ticket=tid) == []
    assert aq_rows(denv) == []


def test_the_guard_holds_a_ticket_that_was_already_being_retried(denv, loop_guard):
    """An older attempt count is the baseline, not an exemption — the burst still has to
    produce something NEW."""
    tid = add_ticket("two real attempts, then a crash loop")
    for n in (1, 2):
        write_attempt(denv, tid, n)
    for _ in range(2):
        crash_pass(denv)

    crash_pass(denv)

    assert log_events(denv, "skip", ticket=tid)[0]["reason"] == "spawn_loop"


def test_spawn_loop_skips_are_not_counted_as_dispatchable(denv, loop_guard):
    """wq#1410: a correctly-refused ticket must not read as a dispatcher stall."""
    tid = add_ticket("loops forever")
    for _ in range(3):
        crash_pass(denv)

    assert log_events(denv, "pass_end")[-1]["dispatchable"] == 0


def test_dry_run_neither_files_a_row_nor_pings(denv, loop_guard):
    tid = add_ticket("loops forever")
    for _ in range(2):
        crash_pass(denv)

    crash_pass(denv, dry_run=True)

    assert log_events(denv, "would_ntfy", ticket=tid)[0]["reason"] == "spawn_loop"
    assert aq_rows(denv) == []
    assert len(ntfy_lines(denv)) == 0


def test_the_ledger_survives_the_utc_day_rollover(denv, loop_guard):
    """lane_counts reset at midnight; a 3h spawn window that reset with them would hand
    a looping ticket a fresh budget every night (§6.3)."""
    tid = add_ticket("spawned either side of midnight")
    for _ in range(2):
        crash_pass(denv)
    st = json.loads((denv["tmp"] / "dstate.json").read_text())
    st["day"] = "2000-01-01"
    (denv["tmp"] / "dstate.json").write_text(json.dumps(st))

    crash_pass(denv)

    assert log_events(denv, "skip", ticket=tid)[0]["reason"] == "spawn_loop"


def test_old_spawns_age_out_of_the_ledger(denv, loop_guard):
    """The guard clears itself: the burst leaves the window and the ticket dispatches
    again, so a transient loop never needs a human to unstick it."""
    tid = add_ticket("loops, then goes quiet")
    for _ in range(2):
        crash_pass(denv)
    path = denv["tmp"] / "dstate.json"
    st = json.loads(path.read_text())
    stale = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat(timespec="seconds")
    for entry in st["spawn_ledger"][str(tid)]:
        entry["ts"] = stale
    path.write_text(json.dumps(st))

    crash_pass(denv)

    assert len(log_events(denv, "spawn", ticket=tid)) == 3
    # the aged-out entries are pruned on load, so the ledger keeps only this pass's spawn
    assert len(json.loads(path.read_text())["spawn_ledger"][str(tid)]) == 1


# ---- wq#824: fail-soft lane validation -----------------------------------------------

def test_bad_lane_does_not_halt_dispatch(denv):
    """ROOT CAUSE REGRESSION: a lane with max_per_day=0 must NOT prevent other lanes
    from dispatching. The bad lane is silently disabled; good lanes proceed."""
    lanes = {
        "testlane": {"enabled": True, "max_per_day": 10},
        "badlane": {"enabled": True, "max_per_day": 0},  # the exact 10h-outage shape
    }
    write_config(denv, lanes=lanes)
    tid = add_ticket("good lane ticket dispatches fine")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    # config_lane error logged for the bad lane
    config_errs = [e for e in log_events(denv, "error") if e["stage"] == "config_lane"]
    assert len(config_errs) == 1
    assert "badlane" in config_errs[0]["reason"]


def test_bad_lane_ntfy_fires(denv):
    """Config validation errors on individual lanes must ntfy Frank loudly."""
    lanes = {
        "testlane": {"enabled": True, "max_per_day": 10},
        "broken": {"enabled": True, "max_per_day": "nope"},
    }
    write_config(denv, lanes=lanes)
    add_ticket("still dispatches")
    assert fd.run_pass() == 0
    lines = ntfy_lines(denv)
    assert len(lines) >= 1
    assert any("bad lane config" in l or "broken" in l for l in lines)


def test_disabled_lane_ignores_max_per_day(denv):
    """enabled:false alone disables a lane — max_per_day is not validated."""
    lanes = {
        "testlane": {"enabled": True, "max_per_day": 10},
        "off": {"enabled": False, "max_per_day": 0},       # was the 10h outage trigger
        "off2": {"enabled": False, "max_per_day": -999},    # absurd value, still fine
        "off3": {"enabled": False},                          # max_per_day omitted entirely
    }
    write_config(denv, lanes=lanes)
    tid = add_ticket("clean dispatch")
    cfg, warnings, _sha = fd.load_config(str(denv["cfg_path"]))
    assert warnings == [], f"disabled lanes should not trigger warnings: {warnings}"
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_bad_lane_object_type(denv):
    """A lane set to a non-dict (e.g. null, string) is disabled, not fatal."""
    lanes = {
        "testlane": {"enabled": True, "max_per_day": 10},
        "junk": "not a dict",
    }
    write_config(denv, lanes=lanes)
    tid = add_ticket("survives junk sibling lane")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    config_errs = [e for e in log_events(denv, "error") if e["stage"] == "config_lane"]
    assert len(config_errs) == 1


def test_bad_lane_enabled_not_bool(denv):
    """A lane with enabled=1 (not a bool) is disabled, not fatal."""
    lanes = {
        "testlane": {"enabled": True, "max_per_day": 10},
        "truthy": {"enabled": 1, "max_per_day": 5},
    }
    write_config(denv, lanes=lanes)
    tid = add_ticket("still goes")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_multiple_bad_lanes_all_reported(denv):
    """Every bad lane gets its own warning; none suppress the others."""
    lanes = {
        "testlane": {"enabled": True, "max_per_day": 10},
        "bad1": {"enabled": True, "max_per_day": 0},
        "bad2": {"enabled": True, "max_per_day": -1},
        "bad3": "not a dict",
    }
    write_config(denv, lanes=lanes)
    tid = add_ticket("good lane still dispatches")
    cfg, warnings, _sha = fd.load_config(str(denv["cfg_path"]))
    assert len(warnings) == 3
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_structural_config_error_still_fails_closed(denv):
    """Top-level structural errors (missing lanes key, bad tight_mode, etc.) still
    fail the entire pass — wq#824 only softened PER-LANE validation."""
    denv["cfg_path"].write_text(json.dumps({
        "max_spawns_per_pass": 3, "cloud_ceiling": 3,
        "tight_mode": {"max_spawns_per_pass": 1, "min_pri": 1},
        "lanes": "not a dict",
    }))
    commit_config(denv["tmp"], "break lanes structurally")
    add_ticket("structural error must still halt")
    assert fd.run_pass() == 1
    errs = log_events(denv, "error")
    assert any(e["stage"] == "config" for e in errs)
    assert lumen_calls(denv) == []


# ---- wq#1408: per-ticket exception boundary, per-repo gate cache, wq() timeout -------
#
# research/NIGHTLY_DISPATCH_RESILIENCE.md §1.5 audit table: every unwrapped per-ticket
# call in _do_pass that could turn one bad ticket/repo into a dead pass. One test per
# row, same shape as test_bad_archetype_skips_ticket_without_crashing_pass: inject the
# exception, assert the rest of the pass still spawns.

def test_profile_unreadable_skips_repo_without_crashing_pass(denv, monkeypatch):
    """§1.5 row :891 — harness_preflight.dispatch_gate_check(repo) itself can raise
    (a JSON typo in any permission-profiles fragment: permission_profiles._load_json is
    a bare json.load). Uncaught, that killed every ticket of the migrated repo, one
    exception at a time, every pass. It must cost the repo's tickets one skip each
    (reason profile_unreadable) from ONE cached exception per repo per pass, one ntfy,
    and a ticket of an unrelated repo must still get a normal decision."""
    broken_repo = denv["repo"]
    other_repo = str(denv["tmp"] / "other_repo")
    os.makedirs(other_repo, exist_ok=True)
    write_config(denv, lanes={
        "testlane": {"enabled": True, "max_per_day": 10},
        "otherlane": {"enabled": True, "max_per_day": 10},
    })
    monkeypatch.setitem(router.LANE_REPO, "otherlane", other_repo)

    calls = []
    real_check = harness_preflight.dispatch_gate_check

    def flaky_gate_check(repo):
        calls.append(repo)
        if repo == broken_repo:
            raise json.JSONDecodeError("bad json", "doc", 0)
        return real_check(repo)
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check", flaky_gate_check)

    t1 = add_ticket("first ticket, broken profile", lane="testlane")
    t2 = add_ticket("second ticket, same broken profile", lane="testlane")
    good = add_ticket("ticket on an unrelated, healthy repo", lane="otherlane")

    assert fd.run_pass() == 0
    assert calls.count(broken_repo) == 1  # cached per repo per pass
    for tid in (t1, t2):
        skips = log_events(denv, "skip", ticket=tid)
        assert len(skips) == 1 and skips[0]["reason"] == "profile_unreadable"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [good]
    assert log_events(denv, "error") == []
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and "unreadable" in lines[0]  # one ntfy per repo per pass


def test_wq_show_timeout_treated_as_vanished_without_crashing_pass(denv, monkeypatch):
    """§1.5 row :755 (router.py:731-732 router.wq()) — no subprocess timeout meant a
    wq/sqlite lock hung the WHOLE pass until systemd's TimeoutStartSec=600 SIGTERMed
    it: no pass_end, no error line, exit 1. router.wq() now bounds every call at 30s;
    a TimeoutExpired on the primary per-ticket wq_show is treated like the ticket
    vanished, costing only that ticket a skip."""
    bad = add_ticket("wq show hangs for this one")
    good = add_ticket("an ordinary candidate in the same pass")

    real_wq_show = router.wq_show

    def flaky_wq_show(tid):
        if int(tid) == bad:
            raise subprocess.TimeoutExpired(cmd=["wq", "show", str(tid)], timeout=30)
        return real_wq_show(tid)
    monkeypatch.setattr(router, "wq_show", flaky_wq_show)

    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=bad)
    assert len(skips) == 1 and skips[0]["reason"] == "vanished"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [good]
    assert log_events(denv, "error") == []


def test_repo_for_ticket_exception_quarantines_tickets_without_crashing_pass(denv, monkeypatch):
    """§1.5 row :780 — router.repo_for_ticket() calls needs.parse()/
    repo_sweep.managed_repos(), either of which can raise on a malformed needs tag or
    an unreadable repo universe. Uncaught, that used to abort the whole pass mid-scan
    (the wq#1368 class, generalized to every unwrapped call). The per-ticket exception
    boundary (wq#1408 §3.1) quarantines only the affected tickets, and pass_end reports
    how many were quarantined."""
    bad1 = add_ticket("repo_for_ticket blows up for this one")
    bad2 = add_ticket("and this one")
    good = add_ticket("an ordinary candidate in the same pass")

    real_repo_for_ticket = router.repo_for_ticket

    def flaky_repo_for_ticket(ticket_id_or_dict):
        t = (ticket_id_or_dict if isinstance(ticket_id_or_dict, dict)
             else router.wq_show(ticket_id_or_dict))
        if int(t["id"]) in (bad1, bad2):
            raise ValueError("malformed needs tag")
        return real_repo_for_ticket(ticket_id_or_dict)
    monkeypatch.setattr(router, "repo_for_ticket", flaky_repo_for_ticket)

    assert fd.run_pass() == 0
    for tid in (bad1, bad2):
        skips = log_events(denv, "skip", ticket=tid)
        assert len(skips) == 1 and skips[0]["reason"] == "quarantined"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [good]
    ends = log_events(denv, "pass_end")
    assert len(ends) == 1 and ends[0]["quarantined"] == 2


def test_router_assign_non_manifest_exception_quarantines_ticket_without_crashing_pass(
        denv, monkeypatch):
    """§1.5 row :839 (router.py:405-407 router.assign non-Manifest paths) —
    candidate_model() only catches SystemExit and ManifestError around router.assign();
    a FileNotFoundError/yaml.YAMLError from an unparseable models.yaml propagates
    further. The per-ticket exception boundary quarantines just that ticket."""
    bad = add_ticket("assign blows up for this one", model=None)
    good = add_ticket("an ordinary candidate in the same pass")

    real_assign = router.assign

    def flaky_assign(tid, **kwargs):
        if int(tid) == bad:
            raise FileNotFoundError("models.yaml vanished")
        return real_assign(tid, **kwargs)
    monkeypatch.setattr(router, "assign", flaky_assign)

    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=bad)
    assert len(skips) == 1 and skips[0]["reason"] == "quarantined"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [good]


def test_pacing_hook_exception_quarantines_ticket_without_crashing_pass(denv, monkeypatch):
    """§1.5 row :862 — PACING_HOOK (pacing.decide) can raise ValueError/KeyError on
    malformed governor limits. Uncaught, that used to abort the whole pass. The
    per-ticket exception boundary quarantines just the affected ticket."""
    bad = add_ticket("pacing hook blows up for this one", pri=5)
    good = add_ticket("an ordinary candidate in the same pass", pri=1)

    real_hook = fd.PACING_HOOK

    def flaky_hook(pri, model, limits, pacing_cfg, exempt):
        if pri == 5:
            raise KeyError("malformed governor limits")
        return real_hook(pri, model, limits, pacing_cfg, exempt)
    monkeypatch.setattr(fd, "PACING_HOOK", flaky_hook)

    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=bad)
    assert len(skips) == 1 and skips[0]["reason"] == "quarantined"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [good]


def test_save_state_exception_quarantines_ticket_without_crashing_pass(denv, monkeypatch):
    """§1.5 row :912 — save_state() (OSError on a full disk) used to propagate straight
    out of the per-ticket loop and abort the whole pass, abandoning every remaining
    candidate. The exception boundary quarantines that ticket's bookkeeping instead;
    the next candidate in the same pass still gets a normal decision."""
    bad = add_ticket("save_state blows up right after this spawns")
    good = add_ticket("an ordinary candidate in the same pass")

    real_save_state = fd.save_state
    calls = []

    def flaky_save_state(state):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return real_save_state(state)
    monkeypatch.setattr(fd, "save_state", flaky_save_state)

    assert fd.run_pass() == 0
    skips = log_events(denv, "skip", ticket=bad)
    assert len(skips) == 1 and skips[0]["reason"] == "quarantined"
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [good]


def test_runaway_stuck_ticket_ids_exception_degrades_to_empty_set(denv, monkeypatch):
    """wq#1408 §3.1 preamble degrade — runaway.stuck_ticket_ids() raising (a broken
    RUNS_ROOT read) used to abort the whole pass before any ticket got a decision. It
    now degrades to the documented safe default (no stuck set) with a logged error
    event, and the pass still runs."""
    def raising_stuck_ticket_ids(threshold=None):
        raise OSError("RUNS_ROOT unreadable")
    monkeypatch.setattr(runaway, "stuck_ticket_ids", raising_stuck_ticket_ids)
    tid = add_ticket("ordinary candidate despite runaway failure")

    assert fd.run_pass() == 0
    errs = [e for e in log_events(denv, "error") if e["stage"] == "runaway"]
    assert len(errs) == 1
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_psi_pressure_check_exception_degrades_to_ok(denv, monkeypatch):
    """wq#1408 §3.1 preamble degrade — psi_pressure.check() raising used to abort the
    whole pass before any ticket got a decision. Degrades to verdict 'ok' with a
    logged error event."""
    def raising_check(tight_cfg):
        raise ValueError("boom")
    monkeypatch.setattr(psi_pressure, "check", raising_check)
    tid = add_ticket("ordinary candidate despite psi failure")

    assert fd.run_pass() == 0
    errs = [e for e in log_events(denv, "error") if e["stage"] == "psi_pressure"]
    assert len(errs) == 1
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_pacing_load_config_exception_degrades_to_default(denv, monkeypatch):
    """wq#1408 §3.1 preamble degrade — pacing.load_config() raising an exception type
    it doesn't already catch internally used to abort the whole pass. Degrades to
    pacing.DEFAULT_CONFIG with a logged error event."""
    def raising_load_config(path=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(pacing, "load_config", raising_load_config)
    tid = add_ticket("ordinary candidate despite pacing config failure")

    assert fd.run_pass() == 0
    errs = [e for e in log_events(denv, "error")
           if e["stage"] == "pacing" and e["reason"] == "pacing_load_failed"]
    assert len(errs) == 1
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_circuit_breaker_exception_logs_error_not_bare_traceback(denv, monkeypatch):
    """wq#1408 §3.1 — circuit_breaker.check_and_trip() used to run OUTSIDE the
    pass-level try/except: an exception there was a bare Python traceback (exit 1,
    nothing in dispatcher.log, invisible even to the health classifier). It now runs
    inside the same try as _do_pass, so an exception is a logged 'error' event like
    every other pass-level failure."""
    def raising_check_and_trip(dry_run=False):
        raise RuntimeError("boom")
    monkeypatch.setattr(circuit_breaker, "check_and_trip", raising_check_and_trip)
    add_ticket("never reached")

    assert fd.run_pass() == 1
    errs = [e for e in log_events(denv, "error") if e["stage"] == "pass"]
    assert len(errs) == 1 and "boom" in errs[0]["reason"]
    assert lumen_calls(denv) == []


# ---- stage 3.7: capability lint in warn mode (wq#1437, BROKER_WIRING.md §2.1) ----

def _lint_roster(monkeypatch):
    """Fixture roster: the lint must never shell out to lumen_state during a test."""
    roster = capability_lint.Roster(names=["lumen", "isis", "thor"], ips={})
    monkeypatch.setattr(capability_lint, "load_roster", lambda refresh=False: roster)
    return roster


def _lint_sweep(monkeypatch):
    """wq#1482: a fresh, hitless read_dir_sweep.json for every real allow.read_dirs
    root, so this file's pre-sweep-gate corpus (dir auto-tag on a cited real allow
    root) is not turned into a permanent unresolved skip by a runtime sweep file
    this test suite never writes."""
    import datetime
    policy = capability_lint.load_policy()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    roots = {r: {"files": 0, "hits": []}
             for r in (policy.get("allow") or {}).get("read_dirs") or []}
    sweep = {"ts": ts, "roots": roots}
    monkeypatch.setattr(capability_lint, "load_sweep", lambda path=None: sweep)
    return sweep


def test_capability_lint_warn_logs_one_event_per_candidate_and_applies_nothing(
        denv, monkeypatch):
    """Slice B is measurement only: every candidate gets exactly one capability_lint
    event carrying the three buckets, the ticket's `needs` column is untouched, and
    the dispatch decision is exactly what it would have been without the lint."""
    _lint_roster(monkeypatch)
    tid = add_ticket("ops: land the dead worktree PR",
                     detail="run lumen-pr land /home/lumen/worktrees/wq1357-a1 5 "
                            "--merge, then restart(tailscaled@thor), then ssh isis")

    assert fd.run_pass() == 0

    lints = log_events(denv, "capability_lint")
    assert len(lints) == 1
    ev = lints[0]
    assert ev["ticket"] == tid and ev["mode"] == "warn" and ev["applied"] is False
    assert ev["refused"] == [
        "dir(/home/lumen/worktrees/wq1357-a1) deny:prefix (/home/lumen/worktrees)",
        "restart(tailscaled@thor) policy:never.nodes (thor)"]
    assert ev["tags"] == ["ssh(isis)"]
    # applies nothing: no needs write, no extra skip, the ticket still spawned
    assert needs_of(denv, tid) == ""
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert not [e for e in log_events(denv, "skip") if e.get("ticket") == tid]


def test_capability_lint_counts_every_candidate_in_pass_end(denv, monkeypatch):
    """A candidate that cites nothing gets no event (one decision line per ticket
    stays true) but is still counted, so `lint_flagged/lint_scanned` is the honest
    slice-C blast-radius number a pass at a time."""
    _lint_roster(monkeypatch)
    add_ticket("plain ticket, cites nothing")
    flagged = add_ticket("ops: ssh isis and read the journal")

    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "capability_lint")] == [flagged]
    end = log_events(denv, "pass_end")[0]
    assert (end["lint_scanned"], end["lint_flagged"]) == (2, 1)


def test_capability_lint_off_flag_silences_the_stage(denv, monkeypatch):
    """Rollback (slice B's flag): FOREMAN_NEEDS_LINT_MODE=off derives nothing and
    logs nothing; dispatch is unchanged."""
    _lint_roster(monkeypatch)
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "off")
    tid = add_ticket("ops: read /home/lumen/worktrees/wq1357-a1")

    assert fd.run_pass() == 0
    assert log_events(denv, "capability_lint") == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_capability_lint_exception_never_costs_a_ticket_its_decision(denv, monkeypatch):
    """A measurement stage that can quarantine a candidate is worse than no stage:
    a lint crash logs capability_lint_error and the ticket dispatches anyway."""
    def boom(*a, **kw):
        raise RuntimeError("lint boom")
    monkeypatch.setattr(capability_lint, "derive", boom)
    tid = add_ticket("ordinary candidate")

    assert fd.run_pass() == 0
    errs = log_events(denv, "capability_lint_error")
    assert len(errs) == 1 and "lint boom" in errs[0]["reason"]
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert not [e for e in log_events(denv, "skip") if e["reason"] == "quarantined"]


# ---- stage 3.7 enforcement: the pre-skip + dir auto-tag (wq#1438, §2.2, slice C) ----
#
# Test-to-failure rows i (unit half), iv, vi and vii from BROKER_WIRING.md §4. Every
# row asserts the negative the doc asks for: lumen_calls(denv) == [] is "no slot was
# burned", which is the entire point of moving the check in front of the spawn.

# Two real directories that are outside every repo ∪ base add_dirs the fixture gives a
# worker, so the lint must derive a dir tag for them (the normalise_dir half of §2.1
# only ever names a directory that EXISTS, so these cannot be invented paths).
REACHABLE_DIR = "/home/lumen/research_prompts"  # wq#1441: must be under allow.read_dirs
SECOND_DIR = "/home/lumen/instructions/playbooks"


def _apply_dir(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply-dir")
    _lint_sweep(monkeypatch)


def test_capability_auto_tags_a_cited_dir_and_still_spawns(denv, monkeypatch):
    """Row (i), unit half: a ticket citing a path outside its reach gets the read
    grant written onto it and dispatches in the SAME pass — the tag is what makes
    fworker.add_dirs_for hand the child `--add-dir /home/lumen/agent`."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket(f"ops: read the notes in {REACHABLE_DIR}")
    add_needs_column(denv)

    assert fd.run_pass() == 0

    assert needs_of(denv, tid) == f"dir({REACHABLE_DIR})"
    assert f"capability_lint: +dir({REACHABLE_DIR})" in notes_of(denv, tid)
    applied = log_events(denv, "capability_applied")
    assert len(applied) == 1 and applied[0]["tags"] == [f"dir({REACHABLE_DIR})"]
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]


def test_cited_secret_is_refused_before_any_spawn(denv, monkeypatch):
    """Row (iv): a ticket citing a token is a Frank-hands ticket. It must be skipped
    with `capability_denied_by_policy`, page once, take no slot, and NEVER have the
    deny-listed path written onto it as a grant."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket("ops: rotate the github token",
                     detail="read /home/lumen/.secrets/github-frank.token first")
    add_needs_column(denv)

    assert fd.run_pass() == 0

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["stage"] == "capability"
    assert skips[0]["reason"] == "capability_denied_by_policy"
    assert ".secrets" in skips[0]["detail"]
    assert lumen_calls(denv) == [] and needs_of(denv, tid) == ""
    assert len(ntfy_lines(denv)) == 1 and f"wq#{tid}" in ntfy_lines(denv)[0]

    # second pass same day: still skipped, not re-pinged (24h dedup, §2.2)
    assert fd.run_pass() == 0
    assert len(log_events(denv, "skip", ticket=tid)) == 2
    assert len(ntfy_lines(denv)) == 1


def test_nonexistent_cited_path_is_unresolved_not_guessed(denv, monkeypatch):
    """Row (iv), second half: the lint will not walk a missing path up to some
    ancestor that does exist — that would be a guess (§2.1). The honest answer is
    `needs_unresolved` with the lint's own text, and no spawn."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket("ops: fix the thing",
                     detail="the config lives in /home/lumen/no-such-dir-at-all/conf")
    add_needs_column(denv)

    assert fd.run_pass() == 0

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "needs_unresolved"
    assert "does not exist" in skips[0]["detail"]
    assert lumen_calls(denv) == [] and needs_of(denv, tid) == ""


def test_v2_verb_stays_unresolved_and_names_the_missing_verb(denv, monkeypatch):
    """§2.4: the tickets that need `sudo`/`reboot`/`adb` stay `needs_unresolved` with
    the v2 tag named in the skip detail. That IS the honest state — better than
    spawning a worker that will discover the same thing after taking a slot."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket("ops: bring the node back", detail="reboot isis after the upgrade")

    assert fd.run_pass() == 0

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "needs_unresolved"
    assert "reboot" in skips[0]["detail"] and "v2 verb" in skips[0]["detail"]
    assert lumen_calls(denv) == []


def test_refusal_beats_a_partial_grant(denv, monkeypatch):
    """Row (vi), second half: a ticket citing one grantable dir AND one deny-listed
    path is refused whole. Never partially granted — the safe half must not be written
    while the unsafe half is what the ticket is actually about."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket("ops: copy secrets into place",
                     detail=f"read {REACHABLE_DIR} then /home/lumen/.ssh/id_ed25519")
    add_needs_column(denv)

    assert fd.run_pass() == 0

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "capability_denied_by_policy"
    assert needs_of(denv, tid) == "" and log_events(denv, "capability_applied") == []
    assert lumen_calls(denv) == []


def test_auto_tag_is_idempotent_and_appends_a_newly_cited_dir(denv, monkeypatch):
    """Row (vi): re-linting an already-tagged ticket writes nothing; a detail edited
    to cite a second path appends exactly that tag, with no duplicate of the first."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket(f"ops: read {REACHABLE_DIR}")
    add_needs_column(denv)

    def reopen():  # the ticket must be a candidate again on the next pass
        db_exec(denv, "UPDATE tasks SET status='open', claimed_by=NULL WHERE id=?", (tid,))

    assert fd.run_pass() == 0
    assert needs_of(denv, tid) == f"dir({REACHABLE_DIR})"

    # second pass, nothing new cited: no second write, no duplicate tag, no note spam
    reopen()
    assert fd.run_pass() == 0
    assert needs_of(denv, tid) == f"dir({REACHABLE_DIR})"
    assert len(log_events(denv, "capability_applied")) == 1
    assert notes_of(denv, tid).count("capability_lint:") == 1

    # detail now cites a second reachable-once-granted path
    db_exec(denv, "UPDATE tasks SET detail=? WHERE id=?",
            (f"also read {SECOND_DIR}/maintenance-window.md", tid))
    reopen()
    assert fd.run_pass() == 0
    assert needs_of(denv, tid) == f"dir({REACHABLE_DIR}), dir({SECOND_DIR})"
    assert len(log_events(denv, "capability_applied")) == 2
    assert notes_of(denv, tid).count("capability_lint:") == 2


def test_capability_skips_are_structural_in_pass_end(denv, monkeypatch):
    """§2.2: all four reasons subtract from `dispatchable` so the watchdog can still
    tell a real stall (dispatchable>0, spawned=0) from a queue of correctly-refused
    tickets (NIGHTLY_DISPATCH_RESILIENCE §3.3.1)."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    add_ticket("secret", detail="cat /home/lumen/secrets/reddit.env")
    add_ticket("ghost", detail="see /home/lumen/no-such-dir-at-all/x")
    ok = add_ticket("ordinary candidate that cites nothing")

    assert fd.run_pass() == 0

    end = log_events(denv, "pass_end")[0]
    assert end["candidates"] == 3 and end["dispatchable"] == 1
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [ok]
    assert set(fd.CAPABILITY_SKIP_REASONS) <= fd.STRUCTURAL_SKIP_REASONS


def test_warn_mode_is_the_rollback(denv, monkeypatch):
    """The slice's documented rollback: FOREMAN_NEEDS_LINT_MODE=warn restores slice B
    — the same ticket the pre-skip refuses above is derived, logged and dispatched."""
    _lint_roster(monkeypatch)
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "warn")
    tid = add_ticket("ops: rotate the token",
                     detail="read /home/lumen/.secrets/github-frank.token")
    add_needs_column(denv)

    assert fd.run_pass() == 0

    assert log_events(denv, "skip", ticket=tid) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert needs_of(denv, tid) == "" and log_events(denv, "capability_lint")


def test_held_ticket_releases_once_its_cited_path_is_granted(denv, monkeypatch):
    """Row (vii), end to end (wq#1401's shape): a ticket held on a
    CITED_PATH_UNREACHABLE failure.json is auto-tagged by stage 3.7 — which runs in
    FRONT of the hold for exactly this reason — and the hold no longer applies, so it
    spawns instead of sitting held forever."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket(f"ops: read {REACHABLE_DIR}")
    add_needs_column(denv)
    d = denv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "failure.json").write_text(json.dumps(
        {"ticket": tid, "triage": "HARNESS_DEFECT", "schema": 2,
         "summary": f"CITED_PATH_UNREACHABLE (cited path {REACHABLE_DIR} outside "
                    f"worktree ∪ add_dirs)",
         "unreachable_paths": [REACHABLE_DIR], "refused": [],
         "inferred_needs": [f"dir({REACHABLE_DIR})"]}))

    assert fd.run_pass() == 0

    assert log_events(denv, "skip", ticket=tid) == []
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert needs_of(denv, tid) == f"dir({REACHABLE_DIR})"


def test_an_unrelated_harness_defect_still_holds_under_the_pre_skip(denv, monkeypatch):
    """The backstop the release must not eat: a defect with no structured paths keeps
    its hold even in apply-dir mode (§2.2 — harness_defect_hold stays for the defects
    the pre-skip cannot see)."""
    _lint_roster(monkeypatch)
    _apply_dir(monkeypatch)
    tid = add_ticket("worktree/repo mismatch, nothing to do with paths")
    write_failure_json(denv, tid, summary="WORKTREE_REPO_MISMATCH")

    assert fd.run_pass() == 0

    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "harness_defect_hold"
    assert lumen_calls(denv) == []


# ---- wq#1409: reprove retry ladder + soft-stale dispatch ---------------------------

def _clean_result(repo):
    return harness_preflight.PreflightResult(repo, ok=True, reason="ok",
                                              static_failures=[], denials=[])


def _errored_result(repo, exc="FileNotFoundError(2, 'No such file or directory')"):
    return harness_preflight.PreflightResult(repo, ok=False,
                                              reason=f"live proof errored: {exc}",
                                              static_failures=[], denials=[])


def test_transient_reprove_error_retries_then_succeeds_without_paging(denv, monkeypatch):
    """(iii) shim fails twice then succeeds: two retries logged, third attempt writes
    the stamp, ticket spawns this pass, zero pages."""
    tid = add_ticket("transient reprove, recovers on 3rd")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    slept = []
    monkeypatch.setattr(fd, "_sleep", slept.append)
    calls = []

    def flaky(repo):
        calls.append(repo)
        return _errored_result(repo) if len(calls) < 3 else _clean_result(repo)
    monkeypatch.setattr(harness_preflight, "preflight", flaky)
    assert fd.run_pass() == 0
    assert len(calls) == 3 and slept == [30, 120]
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [tid]
    assert log_events(denv, "skip", ticket=tid) == []
    retries = [e for e in log_events(denv, "info") if e.get("reason") == "reprove_retry"]
    assert len(retries) == 2 and "FileNotFoundError" in retries[0]["detail"]
    reproved = [e for e in log_events(denv, "info") if e.get("reason") == "auto_reproved"]
    assert len(reproved) == 1 and "attempt 3" in reproved[0]["detail"]
    assert ntfy_lines(denv) == []
    assert "repo" not in fd.load_state()["preflight_ntfy"]


def test_transient_reprove_error_three_times_pages_urgent_with_exception(denv, monkeypatch):
    """(iii-b) shim fails 3x: one urgent page naming the literal error, cooldown set,
    skip reason carries attempts."""
    tid = add_ticket("transient reprove, never recovers")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    monkeypatch.setattr(fd, "_sleep", lambda s: None)
    monkeypatch.setattr(harness_preflight, "preflight", _errored_result)
    assert fd.run_pass() == 0
    lines = ntfy_lines(denv)
    assert len(lines) == 1
    assert "-p urgent" in lines[0] and "FileNotFoundError" in lines[0]
    skips = log_events(denv, "skip", ticket=tid)
    assert len(skips) == 1 and skips[0]["reason"] == "reprove_failed"
    assert "attempts=3" in skips[0]["detail"]
    assert lumen_calls(denv) == []
    entry = fd.load_state()["preflight_ntfy"]["repo"]
    assert entry["attempts"] == 3 and "FileNotFoundError" in entry["last_error"]


def test_persistent_reprove_failure_gets_no_retry(denv, monkeypatch):
    """(iv) a regressed profile: static lint / live denials are persistent — one
    attempt, one high page, no ladder (retrying the same proof cannot help), and the
    denial text surfaces instead of being silently passed."""
    add_ticket("denials survive reprove")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    slept = []
    monkeypatch.setattr(fd, "_sleep", slept.append)
    calls = []

    def denying(repo):
        calls.append(repo)
        return harness_preflight.PreflightResult(
            repo, ok=False, reason="live proof found permission denials",
            static_failures=[], denials=["This command requires approval: npx tsc"])
    monkeypatch.setattr(harness_preflight, "preflight", denying)
    assert fd.run_pass() == 0
    assert len(calls) == 1 and slept == []
    lines = ntfy_lines(denv)
    assert len(lines) == 1 and "-p high" in lines[0] and "npx tsc" in lines[0]
    assert log_events(denv, "spawn") == []


def test_reprove_success_after_page_sends_recovered_ntfy(denv, monkeypatch):
    """§2.2 item 4: a clean proof after a page sends a default-priority Recovered:
    ntfy so the aq row can auto-resolve."""
    add_ticket("first pass errors")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (False, "stale preflight stamp for repo"))
    monkeypatch.setattr(fd, "_sleep", lambda s: None)
    monkeypatch.setattr(harness_preflight, "preflight", _errored_result)
    assert fd.run_pass() == 0
    assert len(ntfy_lines(denv)) == 1
    # expire the cooldown by hand, then recover
    st = fd.load_state()
    st["preflight_ntfy"]["repo"]["ts"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    fd.save_state(st)
    tid2 = add_ticket("second pass recovers")
    monkeypatch.setattr(harness_preflight, "preflight", _clean_result)
    assert fd.run_pass() == 0
    lines = ntfy_lines(denv)
    assert len(lines) == 2 and "-p default" in lines[1] and "Recovered:" in lines[1]
    assert tid2 in [e["ticket"] for e in log_events(denv, "spawn")]
    assert "repo" not in fd.load_state()["preflight_ntfy"]


def test_preflight_ntfy_dict_entries_survive_load_state(denv):
    """load_state's dedup pruning must read the wq#1409 dict shape (and still the old
    plain-string shape) instead of dropping the cooldown on every load."""
    now = datetime.now(timezone.utc)
    fresh_ts = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    old_ts = (now - timedelta(hours=48)).isoformat(timespec="seconds")
    fd.save_state({"day": now.strftime("%Y-%m-%d"), "lane_counts": {},
                   "preflight_ntfy": {"a": {"ts": fresh_ts, "attempts": 3, "paged": True},
                                      "b": fresh_ts,
                                      "c": {"ts": old_ts, "attempts": 1},
                                      "d": {"nots": 1}}})
    st = fd.load_state()
    assert set(st["preflight_ntfy"]) == {"a", "b"}
    assert st["preflight_ntfy"]["a"]["attempts"] == 3


def test_soft_stale_stamp_dispatches_logs_once_and_reseals(denv, monkeypatch):
    """(i) wq#1409 §3.2: profile unchanged, settings churned -> the gate says ok with a
    soft-stale reason; the ticket spawns, ONE stamp_soft_stale info event per repo
    per pass, reseal_stamp is called, preflight() (a live proof) is never called."""
    t1 = add_ticket("soft stale one")
    t2 = add_ticket("soft stale two")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (True, "stamp_soft_stale for repo: hook churn"))
    resealed = []
    monkeypatch.setattr(harness_preflight, "reseal_stamp", lambda repo: resealed.append(repo))

    def boom(repo):
        raise AssertionError("soft-stale must not reprove")
    monkeypatch.setattr(harness_preflight, "preflight", boom)
    assert fd.run_pass() == 0
    assert sorted(e["ticket"] for e in log_events(denv, "spawn")) == sorted([t1, t2])
    soft = [e for e in log_events(denv, "info") if e.get("reason") == "stamp_soft_stale"]
    assert len(soft) == 1 and "hook churn" in soft[0]["detail"]
    assert resealed == [denv["repo"]]
    assert ntfy_lines(denv) == []


def test_soft_stale_dry_run_logs_but_does_not_reseal(denv, monkeypatch):
    add_ticket("soft stale dry")
    monkeypatch.setattr(harness_preflight, "dispatch_gate_check",
                         lambda repo: (True, "stamp_soft_stale for repo"))
    resealed = []
    monkeypatch.setattr(harness_preflight, "reseal_stamp", lambda repo: resealed.append(repo))
    assert fd.run_pass(dry_run=True) == 0
    assert [e for e in log_events(denv, "info") if e.get("reason") == "stamp_soft_stale"]
    assert resealed == []
    assert log_events(denv, "would_spawn")


def test_repo_mismatch_and_route_unassigned_are_structural():
    import foreman_dispatcher as fd
    assert {"repo_mismatch", "route_unassigned"} <= fd.STRUCTURAL_SKIP_REASONS
    assert "tight_pri" not in fd.STRUCTURAL_SKIP_REASONS  # pressure is a real stall signal


# ---- wq#2081: local-pool tickets are exempt from the Anthropic-quota gates ------------

def test_local_ticket_ignores_burn_rate_and_cloud_ceiling(denv):
    """wq#2081: session/weekly nearly spent (critical) and every cloud slot taken. A
    local-pinned ticket still spawns — it draws zero Anthropic quota — while the cloud
    ticket behind it is held by the same gates."""
    denv["gov_state"].write_text(json.dumps(_burn_gov(96, 4, 96, 100, severity="critical")))
    denv["pacing_cfg_path"].write_text(json.dumps(
        {"policy": "off", "min_elapsed_fraction": 0.02, "delay_min_pri": 1,
         "ntfy_dedup_hours": 6}))
    _seed_running(["fworker-wq999991", "fworker-wq999992", "fworker-wq999993"])
    local = add_ticket("local batch item", pri=1, model="qwen36-local")
    add_ticket("cloud item", pri=2)
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [local]
    assert log_events(denv, "burn_rate") == []


def test_full_local_lane_skips_local_only_and_cloud_still_spawns(denv, monkeypatch):
    """wq#2081: at local_ceiling the local ticket is skipped (local_slots_full) instead of
    ending the pass, so the cloud candidate behind it still spawns."""
    write_config(denv, local_ceiling=1)
    _seed_running(["fworker-wq999991"])
    monkeypatch.setattr(fd.concurrency, "is_local_unit", lambda u: u == "fworker-wq999991")
    add_ticket("local overflow", pri=1, model="qwen36-local")
    cloud = add_ticket("cloud rides on", pri=2)
    assert fd.run_pass() == 0
    assert [s["reason"] for s in log_events(denv, "skip")] == ["local_slots_full"]
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [cloud]


# ---- wq#2088: one pass, two partitions, pool-scoped stops -----------------------------

def _pass_end(denv):
    return log_events(denv, "pass_end")[-1]


def test_local_partition_runs_before_cloud_stop(denv):
    """wq#2088: a cloud ticket sorted FIRST hits the cloud ceiling and stops the cloud
    partition (stop no_slots). The local ticket scanned behind it still spawns — the
    old single loop `break`-ed past it."""
    write_config(denv, cloud_ceiling=1)
    _seed_running(["fworker-wq999991"])
    add_ticket("cloud first in scan order", pri=1)
    local = add_ticket("local behind a cloud stop", pri=2, model="qwen36-local")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [local]
    end = _pass_end(denv)
    assert (end["spawned_local"], end["spawned_cloud"]) == (1, 0)
    assert end["stopped_cloud_reason"] == "no_slots" and end["stopped_local_reason"] is None


def test_cloud_deadline_never_cuts_the_local_partition(denv, monkeypatch):
    """wq#2088: the soft pass deadline ends only the cloud partition (halt stage=deadline).
    Local runs first and is not deadline-gated, so a slow/hung cloud side costs local nothing."""
    orig_init = fd._PassCtx.__init__

    def expired_init(self, *a, **kw):
        orig_init(self, *a, **kw)
        self.deadline = float("-inf")  # already past on the first cloud check

    monkeypatch.setattr(fd._PassCtx, "__init__", expired_init)
    write_config(denv)
    add_ticket("cloud past the deadline", pri=1)
    local = add_ticket("local unaffected", pri=2, model="qwen36-local")
    assert fd.run_pass() == 0
    assert [e["ticket"] for e in log_events(denv, "spawn")] == [local]
    end = _pass_end(denv)
    assert (end["spawned_local"], end["spawned_cloud"]) == (1, 0)
    assert end["stopped_cloud_reason"] == "deadline" and end["stopped_local_reason"] is None


def test_local_saturates_past_the_cloud_spawn_cap(denv):
    """wq#2088: max_spawns_per_pass caps the cloud pool only; the local lane fills to
    local_max_spawns_per_pass in one pass even with the cloud cap already spent."""
    write_config(denv, max_spawns_per_pass=1, cloud_ceiling=5, local_ceiling=4,
                 local_max_spawns_per_pass=3)
    cloud = [add_ticket(f"cloud {i}", pri=1) for i in range(2)]
    local = [add_ticket(f"local {i}", pri=2, model="qwen36-local") for i in range(4)]
    assert fd.run_pass() == 0
    spawned = [e["ticket"] for e in log_events(denv, "spawn")]
    assert sorted(spawned) == sorted(local[:3] + cloud[:1])
    end = _pass_end(denv)
    assert (end["spawned_local"], end["spawned_cloud"], end["spawned"]) == (3, 1, 4)
    assert end["stopped_local_reason"] == "spawn_cap" and end["stopped_cloud_reason"] == "spawn_cap"


def test_cloud_gates_never_touch_local(denv):
    """wq#2088: critical burn-rate with the cloud ceiling reached: the cloud partition is
    held (no_slots at the burn-rate ceiling), the local partition spawns untouched."""
    denv["gov_state"].write_text(json.dumps(_burn_gov(96, 4, 96, 100, severity="critical")))
    _seed_running(["fworker-wq999991", "fworker-wq999992"])
    local = [add_ticket(f"local {i}", pri=2, model="qwen36-local") for i in range(2)]
    add_ticket("cloud held", pri=1)
    assert fd.run_pass() == 0
    assert sorted(e["ticket"] for e in log_events(denv, "spawn")) == sorted(local)
    end = _pass_end(denv)
    assert end["dispatchable_local"] == 2 and end["spawned_local"] == 2
    assert end["dispatchable_cloud"] == 1 and end["spawned_cloud"] == 0


def test_pass_end_split_counters(denv):
    """wq#2088: pass_end reports each pool's dispatchable/spawned; unrouted defects are
    counted under neither pool, and the parts sum to `dispatchable`."""
    add_ticket("local one", model="qwen36-local")
    add_ticket("cloud one")
    add_ticket("bad archetype", model=None, archetype="build")
    assert fd.run_pass() == 0
    end = _pass_end(denv)
    assert (end["dispatchable_local"], end["dispatchable_cloud"]) == (1, 1)
    assert (end["spawned_local"], end["spawned_cloud"]) == (1, 1)
    assert end["dispatchable_unpooled"] == 1
    assert end["dispatchable"] == 3 and end["local_slots_full"] == 0


def test_lane_cap_holds_across_partitions_within_one_pass(denv):
    """wq#2088: the gate reads lane_counts before any spawn, so the cap must be enforced
    again at admission or one pass overshoots max_per_day."""
    write_config(denv, lanes={"testlane": {"enabled": True, "max_per_day": 2}})
    for i in range(3):
        add_ticket(f"local {i}", model="qwen36-local")
    assert fd.run_pass() == 0
    assert len(log_events(denv, "spawn")) == 2
    assert [s["reason"] for s in log_events(denv, "skip")] == ["lane_cap"]
    end = _pass_end(denv)
    assert end["spawned_local"] == 2 and end["dispatchable_local"] == 2


def test_local_slots_full_pass_is_healthy_but_local_stall_is_not(denv, monkeypatch):
    """wq#2088: dispatch_health judges each pool. A full local lane (local_slots_full) is
    healthy; local dispatchable>0 with zero local spawns and no full lane is a stall even
    while the cloud pool spawns."""
    import dispatch_health
    write_config(denv, local_ceiling=1)
    _seed_running(["fworker-wq999991"])
    monkeypatch.setattr(fd.concurrency, "is_local_unit", lambda u: u == "fworker-wq999991")
    add_ticket("local overflow", pri=1, model="qwen36-local")
    add_ticket("cloud rides on", pri=2)
    assert fd.run_pass() == 0
    events = [json.loads(l) for l in (denv["tmp"] / "dispatcher.log").read_text().splitlines()
              if l.strip()]
    assert [v for _, _, v, _ in dispatch_health.classify_passes(events)] == ["healthy"]

    events = [dict(e, local_slots_full=0) if e["event"] == "pass_end" else e for e in events]
    verdicts = dispatch_health.classify_passes(events)
    assert [v for _, _, v, _ in verdicts] == ["unhealthy"]
    assert verdicts[0][3].startswith("local spawned=0 dispatchable=1")
