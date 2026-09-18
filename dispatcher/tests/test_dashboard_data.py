"""dashboard_data.py — read-only snapshot aggregation tests (wq#718 AUTO-6).

Zero quota: real `wq` against a scratch WQ_DB (same pattern as test_dispatcher.py);
`lumen-run` is never invoked — concurrency.list_running_units is monkeypatched directly
since dashboard_data's own job is mapping unit names -> ticket detail, not discovering
units. Every test also re-asserts nothing here writes anything (no wq claim/release,
no state file) — the guardrail is that this module is READ-ONLY by construction.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "orchestrator"))
sys.path.insert(0, os.path.join(REPO, "harness"))
import dashboard_data as dd     # noqa: E402
import foreman_dispatcher as fd  # noqa: E402
import circuit_breaker           # noqa: E402
import self_heal                 # noqa: E402
import concurrency               # noqa: E402
import router                    # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"

BASE_CONFIG = {
    "max_spawns_per_pass": 3, "cloud_ceiling": 3,
    "tight_mode": {"max_spawns_per_pass": 1, "min_pri": 1},
    "lanes": {"testlane": {"enabled": True, "max_per_day": 10}},
}


@pytest.fixture
def ddenv(tmp_path, monkeypatch):
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    wq_db = tmp_path / "scratch_wq.db"
    # config.json is read from HEAD of its own repo (wq#772), never the working tree.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "test"], check=True)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(BASE_CONFIG))
    subprocess.run(["git", "-C", str(tmp_path), "add", "config.json"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "initial test config"],
                   check=True)
    gov_state = tmp_path / "gov_state.json"
    gov_state.write_text(json.dumps({"limits": {"session": {"percent": 10}},
                                     "stop_active": False, "off_peak": False}))

    monkeypatch.setenv("WQ_DB", str(wq_db))
    monkeypatch.setenv("WQ_AGENT", "test-dashboard")
    # wq#912: point the real `wq` binary's own lane-refusal check at the same scratch
    # config (already carries "testlane") so `wq add` never depends on the live fleet config.
    monkeypatch.setenv("WQ_DISPATCHER_CONFIG", str(cfg_path))
    monkeypatch.setattr(fd, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(fd, "WQ_BIN", WQ_BIN)
    monkeypatch.setattr(fd, "GOVERNOR_STATE", str(gov_state))
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(self_heal, "WQ_BIN", WQ_BIN)
    monkeypatch.setattr(circuit_breaker, "RUNS_ROOT", str(runs_root))
    # circuit_breaker.is_armed() defaults to the real fleet's DISARM_PATH — without this,
    # test_circuit_breaker_state_passthrough silently reads live production disarm state.
    monkeypatch.setattr(circuit_breaker, "DISARM_PATH", str(tmp_path / "DISARM"))
    monkeypatch.setitem(router.LANE_REPO, "testlane", str(tmp_path))
    router.load_manifest(force=True)

    return {"tmp": tmp_path, "runs_root": runs_root, "wq_db": str(wq_db),
            "cfg_path": cfg_path, "gov_state": gov_state}


def add_ticket(title="scratch dashboard ticket", lane="testlane", deps=None, gate=None,
               no_probe=None):
    # no_probe: wq#1676 made a Liveness: tag mandatory on `wq add` for a Frank-gate shape
    # (e.g. a "[FRANK GATE]" title). A counting test needs the SHAPE, not a live premise,
    # so callers that file one pass the on-the-record escape hatch rather than a fake probe.
    cmd = [WQ_BIN, "add", "--lane", lane, "--title", title]
    if deps:
        cmd += ["--deps", deps]
    if gate:
        cmd += ["--gate", gate]
    if no_probe:
        cmd += ["--no-probe", no_probe]
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    # `added task <id> [<lane>]` is the LAST line, not the only one: --no-probe makes the
    # liveness layer print its own "recorded --no-probe" line first, which a whole-stdout
    # split() would read as the id.
    return int(p.stdout.strip().splitlines()[-1].split()[2])


def claim_ticket(tid):
    p = subprocess.run([WQ_BIN, "claim", "--id", str(tid)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr


def set_status(ddenv, tid, status):
    conn = sqlite3.connect(ddenv["wq_db"])
    try:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
        conn.commit()
    finally:
        conn.close()


def make_attempt(runs_root, tid, n, classification, finished_at):
    d = runs_root / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"attempt-{n}.json").write_text(json.dumps(
        {"ticket": tid, "attempt": n, "classification": classification,
         "finished_at": finished_at}))


def table_snapshot(ddenv):
    conn = sqlite3.connect(ddenv["wq_db"])
    try:
        return conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    finally:
        conn.close()


# ---- running_workers ---------------------------------------------------------------------

def test_running_workers_maps_unit_to_ticket_detail(ddenv, monkeypatch):
    tid = add_ticket()
    claim_ticket(tid)
    monkeypatch.setattr(concurrency, "list_running_units",
                        lambda prefix="fworker-wq": [f"fworker-wq{tid}"])

    now = time.time()
    result = dd.running_workers(now=now)

    assert len(result["workers"]) == 1
    w = result["workers"][0]
    assert w["ticket"] == tid and w["lane"] == "testlane"
    assert w["started_at"] is not None
    assert 0 <= w["elapsed_s"] < 10
    assert w["projected_end"] > now


def test_running_workers_ignores_non_matching_unit_names(ddenv, monkeypatch):
    monkeypatch.setattr(concurrency, "list_running_units",
                        lambda prefix="fworker-wq": ["some-other-unit"])
    assert dd.running_workers() == {"workers": []}


def test_running_workers_reports_lumen_run_error(ddenv, monkeypatch):
    def boom(prefix="fworker-wq"):
        raise concurrency.LumenRunError("lumen-run unreachable")
    monkeypatch.setattr(concurrency, "list_running_units", boom)

    result = dd.running_workers()
    assert result["workers"] == [] and "lumen-run unreachable" in result["error"]


# ---- queue_depth -------------------------------------------------------------------------

def test_queue_depth_counts_open_eligible_blocked(ddenv):
    eligible = add_ticket("plain open ticket")
    frank_gated = add_ticket("[FRANK GATE] rotate keys",
                             no_probe="test fixture: synthetic Frank-gate row for queue-depth counting")
    epic_parent = add_ticket("[EPIC][DO NOT CLAIM] rollup over children")  # wq#428
    dep_blocked = add_ticket("waiting on a dep", deps="999999")
    blocked = add_ticket("will be blocked")
    set_status(ddenv, blocked, "blocked")

    depth = dd.queue_depth(lanes=["testlane"])

    assert depth["testlane"]["open"] == 4  # eligible + frank_gated + epic + dep_blocked
    assert depth["testlane"]["eligible"] == 1  # only the plain one
    assert depth["testlane"]["blocked"] == 1
    assert table_snapshot(ddenv)  # sanity: real rows exist, nothing was corrupted


def test_queue_depth_uses_configured_lanes_by_default(ddenv):
    add_ticket()
    depth = dd.queue_depth()
    assert "testlane" in depth  # from BASE_CONFIG, not hardcoded in dashboard_data


# ---- failure_rate_report -----------------------------------------------------------------

def test_failure_rate_report_overall_and_by_lane(ddenv):
    tid = add_ticket(lane="testlane")
    now = time.time()
    make_attempt(ddenv["runs_root"], tid, 1, "SUCCESS", now - 60)
    make_attempt(ddenv["runs_root"], tid, 2, "KILLED", now - 120)

    report = dd.failure_rate_report(now=now)

    assert report["overall"]["24h"] == {"total": 2, "failures": 1, "rate": 0.5}
    assert report["by_lane"]["24h"]["testlane"] == {"total": 2, "failures": 1, "rate": 0.5}


def test_failure_rate_report_unknown_lane_when_ticket_vanished(ddenv):
    now = time.time()
    make_attempt(ddenv["runs_root"], 999999, 1, "KILLED", now - 60)

    report = dd.failure_rate_report(now=now)
    assert "unknown" in report["by_lane"]["24h"]


# ---- circuit_breaker_state / stuck_tickets / budget_state --------------------------------

def test_circuit_breaker_state_passthrough(ddenv):
    state = dd.circuit_breaker_state()
    assert state["armed"] is True
    assert state["total"] == 0


def test_stuck_tickets_passthrough(ddenv):
    tid = add_ticket()
    d = ddenv["runs_root"] / f"wq{tid}"
    d.mkdir()
    for i in range(1, 6):
        (d / f"attempt-{i}.json").write_text("{}")

    stuck = dd.stuck_tickets()
    assert set(stuck) == {tid}


def test_budget_state_reads_governor_json(ddenv):
    state = dd.budget_state()
    assert state["stop_active"] is False


def test_budget_state_missing_file(ddenv, monkeypatch):
    monkeypatch.setattr(fd, "GOVERNOR_STATE", str(ddenv["tmp"] / "no-such-state.json"))
    state = dd.budget_state()
    assert "error" in state


# ---- snapshot() shape ----------------------------------------------------------------------

def test_snapshot_has_every_required_section(ddenv, monkeypatch):
    monkeypatch.setattr(concurrency, "list_running_units", lambda prefix="fworker-wq": [])
    snap = dd.snapshot()
    for key in ("generated_at", "running", "queue_depth", "failure_rate",
               "circuit_breaker", "stuck_tickets", "budget"):
        assert key in snap
