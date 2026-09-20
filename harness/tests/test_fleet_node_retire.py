"""Design §8 T6 — `fleet-node retire` (wq#2253, C4 of wq#2241).

Seeded registry: hathor (always-on, 2 slots), nephthys (warm, 2 slots), isis (down). FakePorts decides
which health URLs answer and which units are running; retire never gets a way to kill a worker.
"""
import json

import pytest
import yaml

from fleet_node_fakes import Env, start_stub
import node_render as nr


@pytest.fixture(scope="module")
def stub():
    srv = start_stub()
    yield srv
    srv.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch, stub):
    return Env(tmp_path, monkeypatch, stub.server_address[1])


def _refused(env, capsys, *args, want):
    before = env.tracked()
    assert env.cli("retire", *args, "--yes") == 1
    assert want in capsys.readouterr().out
    assert env.tracked() == before, "a refused retire must change nothing"


# --- precheck refusals ----------------------------------------------------------------------------

def test_refuses_the_last_healthy_member_of_the_group(env, capsys):
    env.healthy("nephthys")                          # hathor and isis do not answer their probe
    _refused(env, capsys, "nephthys", want="last healthy member of local-worker")


def test_the_last_member_guard_is_not_waived_by_force(env, capsys):
    env.healthy("hathor")
    _refused(env, capsys, "hathor", "--force", want="last healthy member of local-worker")


def test_refuses_an_always_on_node_without_force(env, capsys):
    env.healthy("hathor", "nephthys")                # another member is healthy, so only the policy blocks
    _refused(env, capsys, "hathor", want="always-on")   # message ends "(Frank requirement 5)"


def test_force_retires_an_always_on_node_when_the_group_survives(env, capsys):
    env.healthy("hathor", "nephthys")
    assert env.cli("retire", "hathor", "--force", "--yes") == 0
    assert env.reg()["nodes"]["hathor"]["state"] == "retired"


def test_a_down_node_retires_without_touching_the_healthy_members(env):
    env.healthy("hathor", "nephthys")
    assert env.cli("retire", "isis", "--yes") == 0
    assert env.reg()["nodes"]["isis"]["state"] == "retired"


def test_refuses_unknown_and_already_retired_nodes(env, capsys):
    assert env.cli("retire", "nowhere", "--yes") == 1
    assert "not in the registry" in capsys.readouterr().out
    env.healthy("hathor", "nephthys")
    assert env.cli("retire", "isis", "--yes") == 0
    assert env.cli("retire", "isis", "--yes") == 1
    assert "already retired" in capsys.readouterr().out


def test_dry_run_prints_five_diffs_and_writes_nothing(env, capsys):
    env.healthy("hathor", "nephthys")
    before = env.tracked()
    assert env.cli("retire", "nephthys") == 0
    out = capsys.readouterr().out
    assert all(f"== {n}/5 " in out for n in range(1, 6))
    assert "-  - id: nephthys-qwen14b" in out and "nephthys-qwen14b" in out
    assert env.tracked() == before


# --- drain ----------------------------------------------------------------------------------------

def test_a_running_unit_drains_first_and_is_never_killed(env, capsys):
    env.healthy("hathor", "nephthys")
    polls, seen = [], {}

    def running():
        polls.append(1)
        reg = env.reg()
        seen.setdefault("state", reg["nodes"]["nephthys"]["state"])
        seen.setdefault("backends", json.loads(env.read("dispatcher/config.json"))["local_backends"])
        return ["nephthys-qwen14b@low"] if len(polls) < 4 else []      # a worker finishes on the 4th look

    env.ports.running = running
    assert env.cli("retire", "nephthys", "--yes") == 0
    assert seen["state"] == "draining"                                  # set before anything waits
    assert "nephthys-qwen14b" not in seen["backends"]                   # admits nothing new
    assert seen["backends"]["local-worker"]["group"] == ["hathor-llama8b", "qwen36-local"]   # isis stays: down
    assert env.ports.now >= 20                                          # it waited for the worker (virtual time)
    assert set(env.ports.names()) <= {"restart_gateway"}                # no stop/kill/unserve of any kind
    assert env.reg()["nodes"]["nephthys"]["state"] == "retired"


def test_the_retired_row_is_kept_with_its_measured_numbers(env):
    env.healthy("hathor", "nephthys")
    before = env.reg()["nodes"]["nephthys"]
    assert env.cli("retire", "nephthys", "--yes") == 0
    after = env.reg()["nodes"]["nephthys"]
    assert after["state"] == "retired" and after["retired_at"]
    assert after["models"] == before["models"]                          # prefill_tps 7.4, served_ctx, capabilities
    assert after["models"][0]["prefill_tps"] == 7.4
    assert (after["tailscale_ip"], after["plug_id"]) == (before["tailscale_ip"], before["plug_id"])
    assert "nephthys" not in env.read("dispatcher/config.json").split('"local-worker"')[1]
    assert "id: nephthys-qwen14b" not in env.read("research/models.yaml")
    assert "127.0.0.1" not in open(env.litellm).read() and "100.98.74.87" not in open(env.litellm).read()
    assert "nephthys-qwen14b" not in env.ports.key and "hathor-llama8b" in env.ports.key
    assert nr.drift(env.reg(), env.root) == {}


def test_a_worker_that_never_finishes_rolls_back_to_live_without_touching_it(env, capsys):
    env.healthy("hathor", "nephthys")
    before = env.tracked()
    env.ports.running = lambda: ["nephthys-qwen14b"]
    assert env.cli("retire", "nephthys", "--yes", "--drain-deadline-s", "60") == 1
    out = capsys.readouterr().out
    assert "still running on nephthys" in out and "never killing a worker" in out
    assert env.tracked() == before                                      # state live again, all targets re-rendered
    assert env.reg()["nodes"]["nephthys"]["state"] == "live"
    assert set(env.ports.names()) <= {"restart_gateway"}


def test_a_phase_6_failure_rolls_the_state_back_to_live(env, capsys):
    env.healthy("hathor", "nephthys")
    before = env.tracked()
    env.ports.fail_key_apply = True
    assert env.cli("retire", "nephthys", "--yes") == 1
    assert "FAIL phase" in capsys.readouterr().out
    assert env.reg()["nodes"]["nephthys"]["state"] == "live"
    assert env.tracked() == before


# --- rollback -------------------------------------------------------------------------------------

def test_rollback_restores_state_live_in_one_render(env):
    env.healthy("hathor", "nephthys")
    pre_dispatcher = env.read("dispatcher/config.json")
    pre_key = list(env.ports.key)
    assert env.cli("retire", "nephthys", "--yes") == 0
    assert env.reg()["nodes"]["nephthys"]["state"] == "retired"
    assert env.cli("retire", "nephthys", "--rollback") == 0
    row = env.reg()["nodes"]["nephthys"]
    assert row["state"] == "live" and row["models"][0]["prefill_tps"] == 7.4
    assert env.read("dispatcher/config.json") == pre_dispatcher
    assert sorted(env.ports.key) == sorted(pre_key)
    assert nr.drift(env.reg(), env.root) == {}


def test_rollback_with_no_saved_run_is_an_error(env, capsys):
    assert env.cli("retire", "nephthys", "--rollback") == 1
    assert "no saved retire run" in capsys.readouterr().out


# --- power ----------------------------------------------------------------------------------------

def test_power_off_is_reported_as_skipped_until_the_power_down_sequence_exists(env, capsys):
    env.healthy("hathor", "nephthys")
    assert env.cli("retire", "nephthys", "--power", "off", "--yes") == 0
    assert "--power off skipped" in capsys.readouterr().out
    assert yaml.safe_load(open(env.registry).read())["nodes"]["nephthys"]["state"] == "retired"
