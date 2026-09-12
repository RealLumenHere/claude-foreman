"""spawner.py — concurrency-capped, staggered batch dispatch. BUILD_PLAN.md §T2b
(wq#616). Zero quota: fake `lumen-run` (no real systemd unit / claude process), fake
governor, scratch wq db — mirrors the fixture style test_fworker_integration.py and
test_settings_lint.py already use.
"""
import json
import os
import stat
import subprocess
import sys
import time

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ORCH_DIR = os.path.normpath(os.path.join(HARNESS_DIR, "..", "orchestrator"))
sys.path.insert(0, HARNESS_DIR)
sys.path.insert(0, ORCH_DIR)
import concurrency  # noqa: E402
import spawner  # noqa: E402
import router  # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"
FAKE_GOVERNOR = os.path.join(TESTS_DIR, "fake_governor.py")


def install_fake_lumen_run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "lumen-run"
    wrapper.write_text(
        f"#!/usr/bin/env bash\nexec python3 {TESTS_DIR}/fake_lumen_run.py \"$@\"\n")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(bin_dir), str(wrapper)


@pytest.fixture
def spawner_env(tmp_path, monkeypatch):
    bin_dir, lumen_run = install_fake_lumen_run(tmp_path)
    state_file = tmp_path / "units.json"
    state_file.write_text("[]")
    log_file = tmp_path / "calls.jsonl"
    wq_db = tmp_path / "scratch_wq.db"

    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_LUMEN_RUN_STATE", str(state_file))
    monkeypatch.setenv("FAKE_LUMEN_RUN_LOG", str(log_file))
    monkeypatch.setenv("WQ_DB", str(wq_db))
    monkeypatch.setenv("WQ_AGENT", "test-spawner")
    monkeypatch.setenv("FOREMAN_SLOT_POLL_S", "0.1")

    concurrency.LUMEN_RUN_BIN = lumen_run
    spawner.GOVERNOR_CLI = FAKE_GOVERNOR
    spawner.SPAWN_RESERVE_DB = str(tmp_path / "spawn_reserve.db")  # wq#1815: isolated per test
    router.load_manifest(force=True)  # fresh manifest cache under the new env

    return {"state_file": state_file, "log_file": log_file, "wq_db": str(wq_db),
            "repo": str(tmp_path)}


def add_ticket(title, model="claude-sonnet-5", effort="high"):
    p = subprocess.run([WQ_BIN, "add", "--title", title, "--model", model, "--effort", effort],
                        capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def read_calls(log_file):
    if not os.path.exists(log_file):
        return []
    with open(log_file) as f:
        return [json.loads(l) for l in f if l.strip()]


def test_dispatches_within_ceiling(spawner_env):
    t1 = add_ticket("scratch spawner ticket 1")
    t2 = add_ticket("scratch spawner ticket 2")

    results = spawner.dispatch_batch([t1, t2], spawner_env["repo"], ceiling=3,
                                      stagger_min=0.05, stagger_max=0.1)
    assert results[t1] is not None and results[t1].startswith("UNIT=")
    assert results[t2] is not None and results[t2].startswith("UNIT=")

    calls = read_calls(spawner_env["log_file"])
    assert len(calls) == 2


def test_stagger_delay_between_spawns(spawner_env):
    t1 = add_ticket("scratch spawner stagger 1")
    t2 = add_ticket("scratch spawner stagger 2")

    t0 = time.time()
    spawner.dispatch_batch([t1, t2], spawner_env["repo"], ceiling=3,
                            stagger_min=0.3, stagger_max=0.4)
    elapsed = time.time() - t0
    assert elapsed >= 0.3, f"expected >=0.3s stagger between 2 spawns, took {elapsed:.2f}s"


def test_ceiling_blocks_until_slot_frees_then_gives_up(spawner_env):
    # pre-seed 3 units already "running" at the ceiling; a 4th ticket has nowhere to go
    # and no slot ever frees in this test, so dispatch must give up (not hang forever)
    # and must NEVER call lumen-run start for it.
    spawner_env["state_file"].write_text(json.dumps(
        ["fworker-wq901", "fworker-wq902", "fworker-wq903"]))
    t4 = add_ticket("scratch spawner ceiling ticket")

    results = spawner.dispatch_batch([t4], spawner_env["repo"], ceiling=3,
                                      slot_wait_max_s=0.3)
    assert results[t4] is None

    calls = read_calls(spawner_env["log_file"])
    assert calls == [], "must not spawn past the ceiling"


def test_slot_frees_mid_wait_then_dispatches(spawner_env):
    # 3 units at the ceiling of 3; simulate one finishing (state file shrinks) shortly
    # after the poll loop starts — dispatch should pick up the freed slot rather than
    # timing out.
    spawner_env["state_file"].write_text(json.dumps(
        ["fworker-wq901", "fworker-wq902", "fworker-wq903"]))
    t = add_ticket("scratch spawner slot-frees ticket")

    import threading

    def free_a_slot():
        time.sleep(0.2)
        spawner_env["state_file"].write_text(json.dumps(["fworker-wq901", "fworker-wq902"]))

    threading.Thread(target=free_a_slot, daemon=True).start()
    results = spawner.dispatch_batch([t], spawner_env["repo"], ceiling=3, slot_wait_max_s=5)
    assert results[t] is not None and results[t].startswith("UNIT=")


def test_governor_deny_skips_without_spawning(spawner_env, monkeypatch):
    monkeypatch.setenv("FAKE_GOVERNOR_DENY", "always")
    t = add_ticket("scratch spawner governor-deny ticket")

    results = spawner.dispatch_batch([t], spawner_env["repo"], ceiling=3)
    assert results[t] is None

    calls = read_calls(spawner_env["log_file"])
    assert calls == [], "governor deny must prevent the lumen-run start call"


def test_double_spawn_reservation_blocks_racing_duplicate(spawner_env):
    """wq#1815 (PHASE0_WIRING_SPEC E.5): two concurrent dispatch_one() calls for the same
    ticket race to reserve the same (ticket_id, attempt) key via despag's control_sqlite
    (wq#1807) idempotency reserve. Exactly one may reach `lumen-run start`; the other must
    lose the reservation and return None before Popen. This is the dispatcher-spawn-path
    class fix for the double-spawn incidents ISSUE-LOG #4 logged at the wq-claim layer
    (wq#1141, wq#1097 x2, fixed there by PR#46's lease+fence)."""
    import threading

    t = add_ticket("scratch spawner double-spawn ticket")

    results = [None, None]

    def run(i):
        results[i] = spawner.dispatch_one(t, spawner_env["repo"])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    calls = read_calls(spawner_env["log_file"])
    assert len(calls) == 1, f"expected exactly one lumen-run start call, got {len(calls)}"
    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1 and winners[0].startswith("UNIT=")
    assert len(losers) == 1


def test_mixed_allow_and_deny_batch(spawner_env, monkeypatch):
    # first ticket allowed, second denied — batch must not abort early, and must
    # report each ticket's own outcome independently.
    t_ok = add_ticket("scratch spawner mixed ok ticket")
    t_deny = add_ticket("scratch spawner mixed deny ticket")

    real_governor_allows = spawner.governor_allows
    calls_seen = []

    def fake_allows(model):
        calls_seen.append(model)
        return len(calls_seen) == 1  # allow first call, deny the rest

    monkeypatch.setattr(spawner, "governor_allows", fake_allows)
    results = spawner.dispatch_batch([t_ok, t_deny], spawner_env["repo"], ceiling=3,
                                      stagger_min=0.05, stagger_max=0.1)
    assert results[t_ok] is not None
    assert results[t_deny] is None
    monkeypatch.setattr(spawner, "governor_allows", real_governor_allows)
