"""wq#2130: per-backend local capacity + per-pool circuit breaker, through a full pass.

Reuses test_dispatcher.py's `denv` fixture and helpers (real `wq` against a scratch DB, the
fake lumen-run) like test_fire_drill.py does. Health probes are stubbed; the spawn stub adds
each new worker to the fake running-units list, which is what the real lumen-run does, so a
later admission in the same pass sees earlier spawns.
"""
import json
import os
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
from test_dispatcher import (  # noqa: E402,F401
    denv, add_ticket, log_events, write_config, _seed_running, _pass_end, fd,
    local_capacity, router, spawner)

HATHOR_URL = "http://hathor.test/api/ps"
NEPH_URL = "http://nephthys.test/api/ps"
BACKENDS = {
    "hathor-llama8b": {"health_url": HATHOR_URL, "slots": 2, "served_ctx": 32768,
                       "first_frame_s": 1500},
    "nephthys-qwen14b": {"health_url": NEPH_URL, "slots": 2, "served_ctx": 32768,
                         "first_frame_s": 1500},
    "local-worker": {"group": ["hathor-llama8b", "nephthys-qwen14b"], "served_ctx": 32768,
                     "first_frame_s": 1500},
}


def probe_up(monkeypatch, *urls):
    up = set(urls)
    monkeypatch.setattr(local_capacity, "http_probe", lambda url, timeout=2.0: url in up)


def spawn_stub(denv, monkeypatch):
    """Stand-in for spawner.dispatch_one. Returns the [(ticket, env-kwarg)] call list."""
    calls = []

    def dispatch_one(tid, repo, env=None):
        units = json.loads(denv["units_state"].read_text())
        units.append(f"fworker-wq{tid}")
        denv["units_state"].write_text(json.dumps(units))
        calls.append((tid, env))
        return f"lumen-job-fworker-wq{tid}-1"

    monkeypatch.setattr(spawner, "dispatch_one", dispatch_one)
    return calls


def write_attempts(denv, tid_base, n, classification, model):
    for i in range(n):
        tid = tid_base + i
        d = denv["runs_root"] / f"wq{tid}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "attempt-1.json").write_text(json.dumps(
            {"ticket": tid, "attempt": 1, "classification": classification,
             "finished_at": time.time() - 60, "model": model}))


def pause_path(denv):
    return str(denv["tmp"] / "local_pause.json")


# ---- capacity ----------------------------------------------------------------------------

def test_backend_never_admits_more_than_its_slots(denv, monkeypatch):
    """hathor has 2 slots: five hathor-pinned tickets spawn exactly two in one pass (each
    spawn is visible to the next admission); the other three are local_slots_full skips."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    calls = spawn_stub(denv, monkeypatch)
    for i in range(5):
        add_ticket(f"hathor item {i}", model="hathor-llama8b")
    assert fd.run_pass() == 0
    assert len(calls) == 2
    assert [s["reason"] for s in log_events(denv, "skip")] == ["local_slots_full"] * 3
    end = _pass_end(denv)
    assert end["spawned_local"] == 2 and end["local_slots_full"] == 3


def test_backends_are_sized_independently(denv, monkeypatch):
    """A full hathor does not block nephthys (the single ceiling counted them together)."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    calls = spawn_stub(denv, monkeypatch)
    for i in range(3):
        add_ticket(f"hathor item {i}", model="hathor-llama8b", pri=1)
    neph = add_ticket("nephthys item", model="nephthys-qwen14b", pri=2)
    assert fd.run_pass() == 0
    assert len(calls) == 3 and neph in [t for t, _ in calls]


def test_failed_probe_gives_zero_capacity_and_cloud_still_spawns(denv, monkeypatch):
    """Capacity is 0 when the probe fails; the cloud ticket behind it is untouched."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, NEPH_URL)  # hathor's probe fails
    calls = spawn_stub(denv, monkeypatch)
    add_ticket("hathor item", model="hathor-llama8b", pri=1)
    cloud = add_ticket("cloud item", pri=2)
    assert fd.run_pass() == 0
    assert [t for t, _ in calls] == [cloud]
    assert [s["reason"] for s in log_events(denv, "skip")] == ["local_slots_full"]


def test_free_slots_is_zero_when_probe_fails_and_slots_minus_running_otherwise():
    running = ["hathor-llama8b", "nephthys-qwen14b@low"]
    up = {"http://hathor.test/api/ps"}
    cap = local_capacity.LocalCapacity(BACKENDS, lambda: running, probe=lambda u: u in up)
    assert cap.free_slots("hathor-llama8b") == 1
    assert cap.free_slots("nephthys-qwen14b") == 0      # probe fails
    assert cap.free_slots("local-worker") == 1          # only hathor is healthy: 2 - 1
    assert cap.free_slots("nobody") == 0 and not cap.knows("nobody")


def test_probe_runs_once_per_pass():
    seen = []
    cap = local_capacity.LocalCapacity(BACKENDS, lambda: [],
                                       probe=lambda u: seen.append(u) or True)
    for _ in range(5):
        cap.free_slots("hathor-llama8b")
        cap.free_slots("local-worker")
    assert sorted(seen) == [HATHOR_URL, NEPH_URL]


def test_cooldown_file_zeroes_a_backend_until_it_expires(tmp_path):
    cd = tmp_path / "cd.json"
    cd.write_text(json.dumps({"hathor-llama8b": {"until": time.time() + 600},
                              "nephthys-qwen14b": time.time() - 5}))
    cap = local_capacity.LocalCapacity(BACKENDS, lambda: [], probe=lambda u: True,
                                       cooldown_path=str(cd))
    assert cap.free_slots("hathor-llama8b") == 0
    assert cap.free_slots("nephthys-qwen14b") == 2


def test_backend_cooldown_gives_zero_capacity_in_a_pass(denv, monkeypatch):
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    (denv["tmp"] / "backend_cooldown.json").write_text(json.dumps(
        {"hathor-llama8b": {"until": time.time() + 600}}))
    calls = spawn_stub(denv, monkeypatch)
    add_ticket("hathor item", model="hathor-llama8b")
    assert fd.run_pass() == 0
    assert calls == []


def test_static_lever_falls_back_to_local_ceiling(denv, monkeypatch):
    """FOREMAN_LOCAL_CAPACITY=static ignores local_backends (even failed probes) and bounds
    the local rung by local_ceiling alone, exactly as before."""
    monkeypatch.setenv("FOREMAN_LOCAL_CAPACITY", "static")
    write_config(denv, local_backends=BACKENDS, local_ceiling=3)
    probe_up(monkeypatch)  # every probe fails
    calls = spawn_stub(denv, monkeypatch)
    for i in range(5):
        add_ticket(f"hathor item {i}", model="hathor-llama8b")
    assert fd.run_pass() == 0
    assert len(calls) == 3 and all(env is None for _, env in calls)


def test_local_ceiling_still_clamps_live_capacity(denv, monkeypatch):
    write_config(denv, local_backends=BACKENDS, local_ceiling=1)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    calls = spawn_stub(denv, monkeypatch)
    for i in range(3):
        add_ticket(f"hathor item {i}", model="hathor-llama8b")
    assert fd.run_pass() == 0
    assert len(calls) == 1


def test_group_capacity_is_the_sum_of_healthy_members(denv, monkeypatch):
    """`local-worker` = sum of healthy members' slots: nephthys down -> 2, not 4."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL)
    calls = spawn_stub(denv, monkeypatch)
    for i in range(4):
        add_ticket(f"group item {i}", model="local-worker")
    assert fd.run_pass() == 0
    assert len(calls) == 2


def test_local_spawn_carries_first_frame_and_served_ctx_env(denv, monkeypatch):
    """A Mac-local spawn passes FOREMAN_FIRST_FRAME_S / FOREMAN_SERVED_CTX; a cloud spawn
    keeps the plain 2-argument call."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    calls = spawn_stub(denv, monkeypatch)
    local = add_ticket("hathor item", model="hathor-llama8b", pri=1)
    cloud = add_ticket("cloud item", pri=2)
    assert fd.run_pass() == 0
    assert dict(calls) == {local: {"FOREMAN_FIRST_FRAME_S": "1500",
                                   "FOREMAN_SERVED_CTX": "32768"}, cloud: None}


def test_group_env_uses_the_smallest_healthy_member_ctx():
    backends = json.loads(json.dumps(BACKENDS))
    backends["nephthys-qwen14b"]["served_ctx"] = 8192
    cap = local_capacity.LocalCapacity(backends, lambda: [], probe=lambda u: True)
    assert cap.spawn_env("local-worker")["FOREMAN_SERVED_CTX"] == "8192"
    cap = local_capacity.LocalCapacity(backends, lambda: [], probe=lambda u: u == HATHOR_URL)
    assert cap.spawn_env("local-worker")["FOREMAN_SERVED_CTX"] == "32768"


def test_dispatch_cmd_puts_env_in_front_of_the_worker(denv):
    """lumen-run forwards only PATH, so per-spawn env rides as `env K=V ...`."""
    tid = add_ticket("hathor item", model="hathor-llama8b")
    plain = router.dispatch_cmd(tid, denv["repo"])
    with_env = router.dispatch_cmd(tid, denv["repo"], env={"FOREMAN_SERVED_CTX": "32768",
                                                           "FOREMAN_FIRST_FRAME_S": "1500"})
    i = with_env.index("env")
    assert with_env[i + 1:i + 3] == ["FOREMAN_FIRST_FRAME_S=1500", "FOREMAN_SERVED_CTX=32768"]
    assert with_env[i + 3:] == plain[plain.index("python3"):]


# ---- partition isolation -----------------------------------------------------------------

@pytest.mark.parametrize("model", ["hathor-llama8b", "qwen36-local", "local-worker",
                                   "deepseek-v4-flash"])
@pytest.mark.parametrize("mode", ["live", "static"])
def test_local_ticket_never_reaches_admit_cloud(denv, monkeypatch, model, mode):
    """Regression (spec §1.1, governor/burn-rate/pacing row): every exempt-pool model —
    Mac-local, the local-worker group, the cheap_cloud class — is admitted by _admit_local
    only; _admit_cloud (governor, burn-rate, pacing, cloud_ceiling) is never asked."""
    monkeypatch.setenv("FOREMAN_LOCAL_CAPACITY", mode)
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    asked = []
    monkeypatch.setattr(fd, "_admit_cloud", lambda ctx, r: asked.append(r["model"]) or False)
    calls = spawn_stub(denv, monkeypatch)
    add_ticket("local item", model=model)
    assert fd.run_pass() == 0
    assert asked == []
    assert len(calls) == 1


def test_cheap_cloud_is_not_a_mac_slot(denv, monkeypatch):
    """deepseek-v4-flash stays governor-exempt (local partition) but has no local_backends
    row: Mac slots neither bound it nor are consumed by it."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    calls = spawn_stub(denv, monkeypatch)
    for i in range(3):
        add_ticket(f"deepseek {i}", model="deepseek-v4-flash", pri=1)
    for i in range(2):
        add_ticket(f"hathor {i}", model="hathor-llama8b", pri=2)
    assert fd.run_pass() == 0
    assert len(calls) == 5


# ---- local pause + per-pool breaker -------------------------------------------------------

def test_local_pause_stops_the_local_partition_only(denv, monkeypatch):
    """An active local_pause.json ends the local partition (stage=local_pause), the cloud
    partition spawns, and dispatch_health does not call the paused pool a stall."""
    import dispatch_health
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    local_capacity.write_pause("test trip", {}, path=pause_path(denv))
    calls = spawn_stub(denv, monkeypatch)
    add_ticket("hathor item", model="hathor-llama8b", pri=1)
    cloud = add_ticket("cloud item", pri=2)
    assert fd.run_pass() == 0
    assert [t for t, _ in calls] == [cloud]
    end = _pass_end(denv)
    assert end["local_paused"] is True and end["stopped_local_reason"] == "local_paused"
    assert [h["stage"] for h in log_events(denv, "halt")] == ["local_pause"]
    events = [json.loads(line) for line in (denv["tmp"] / "dispatcher.log").read_text()
              .splitlines() if line.strip()]
    assert [v for _, _, v, _ in dispatch_health.classify_passes(events)] == ["healthy"]


def test_expired_local_pause_is_ignored(denv, monkeypatch):
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    local_capacity.write_pause("old trip", {}, now=time.time() - 7200, path=pause_path(denv))
    calls = spawn_stub(denv, monkeypatch)
    add_ticket("hathor item", model="hathor-llama8b")
    assert fd.run_pass() == 0
    assert len(calls) == 1


def test_local_breaker_trip_pauses_local_and_leaves_cloud_running(denv, monkeypatch):
    """The local pool's own window trips (8/8 KILLED on hathor). The pass writes
    local_pause.json and NO DISPATCH_DISARM, and the cloud ticket still spawns."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    write_attempts(denv, 5000, 8, "KILLED", "hathor-llama8b")
    calls = spawn_stub(denv, monkeypatch)
    add_ticket("hathor item", model="hathor-llama8b", pri=1)
    cloud = add_ticket("cloud item", pri=2)
    assert fd.run_pass() == 0
    assert not (denv["tmp"] / "DISARM").exists()
    pause = local_capacity.read_pause(path=pause_path(denv))
    assert pause is not None and 3500 < pause["until"] - pause["tripped_at"] < 3700
    assert [t for t, _ in calls] == [cloud]
    assert all(h["stage"] != "circuit_breaker" for h in log_events(denv, "halt"))


def test_local_failures_alone_never_disarm_the_fleet(denv, monkeypatch):
    """Under the old global window, 20 local KILLED attempts and no cloud attempts read as a
    100% fleet failure rate and wrote DISPATCH_DISARM. The cloud window is now empty."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    write_attempts(denv, 6000, 20, "KILLED", "nephthys-qwen14b")
    cloud = add_ticket("cloud item")
    calls = spawn_stub(denv, monkeypatch)
    assert fd.run_pass() == 0
    assert not (denv["tmp"] / "DISARM").exists()
    assert [t for t, _ in calls] == [cloud]


def test_cloud_breaker_trip_still_disarms_everything(denv, monkeypatch):
    """A cloud trip is today's behaviour: DISARM written, pass ended, local partition too."""
    write_config(denv, local_backends=BACKENDS)
    probe_up(monkeypatch, HATHOR_URL, NEPH_URL)
    write_attempts(denv, 7000, 8, "KILLED", "claude-sonnet-5")
    calls = spawn_stub(denv, monkeypatch)
    add_ticket("hathor item", model="hathor-llama8b")
    add_ticket("cloud item")
    assert fd.run_pass() == 0
    assert (denv["tmp"] / "DISARM").exists()
    assert calls == []
    assert local_capacity.read_pause(path=pause_path(denv)) is None


def test_static_lever_restores_the_global_breaker_window(denv, monkeypatch):
    """FOREMAN_LOCAL_CAPACITY=static: one global window again — local failures trip DISARM
    and no local pause is written (the pre-wq#2130 behaviour)."""
    monkeypatch.setenv("FOREMAN_LOCAL_CAPACITY", "static")
    write_config(denv, local_backends=BACKENDS)
    write_attempts(denv, 8000, 8, "KILLED", "hathor-llama8b")
    add_ticket("cloud item")
    assert fd.run_pass() == 0
    assert (denv["tmp"] / "DISARM").exists()
    assert local_capacity.read_pause(path=pause_path(denv)) is None
