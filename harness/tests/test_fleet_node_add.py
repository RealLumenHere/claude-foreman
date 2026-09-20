"""Design §8 T5 — `fleet-node add` (wq#2253, C4 of wq#2241).

Stub node: a real loopback HTTP health stub, a FakePorts standing in for ssh / plug-ctl / fleet-inv / the
bench / the gateway (fleet_node_fakes.py). Nothing here touches the live fleet or the live repo files.
"""
import json
import math
import os

import pytest
import yaml

from fleet_node_fakes import GOOD_BENCH, Env, start_stub
import node_lifecycle as nl
import registry_edit as ed

ADD = ("seshat", "--family", "qwen36-local", "--slots", "2")


@pytest.fixture(scope="module")
def stub():
    srv = start_stub()
    yield srv
    srv.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch, stub):
    return Env(tmp_path, monkeypatch, stub.server_address[1])


# --- precheck: read-only, exit 1, nothing changed -------------------------------------------------

def _refused(env, capsys, *args, want):
    before = env.tracked()
    assert env.cli("add", *args, "--yes") == 1
    out = capsys.readouterr().out
    assert want in out
    assert env.tracked() == before, "a failed precheck must change nothing"
    assert "serve" not in env.ports.names()


def test_precheck_rejects_a_node_with_no_plug_id(env, capsys):
    del env.ports.plug_map["seshat"]
    _refused(env, capsys, *ADD, want="no plug_id in `fleet-inv plugs`")


def test_precheck_rejects_a_node_name_that_breaks_the_naming_rule(env, capsys):
    env.ports.plug_map["bad@node"], env.ports.ips["bad@node"] = "P9", "127.0.0.1"
    _refused(env, capsys, "bad@node", "--family", "qwen36-local", want="breaks design §4")


def test_precheck_rejects_a_family_without_a_governor_prefix(env, capsys):
    text = open(env.registry).read().replace("\nnodes:\n", "\n  gemma3-27b: {blob: g.gguf, serving: ollama, "
                                             "port: 11434, health_path: /api/ps, swap: implicit}\nnodes:\n", 1)
    open(env.registry, "w").write(text)
    _refused(env, capsys, "seshat", "--family", "gemma3-27b", want="wq#2256")


def test_precheck_rejects_a_blob_sha_mismatch(env, capsys):
    env.ports.shas["seshat"] = "b" * 64
    _refused(env, capsys, *ADD, want="blob sha256 mismatch")


def test_precheck_rejects_a_missing_gguf_on_the_target(env, capsys):
    del env.ports.shas["seshat"]
    _refused(env, capsys, *ADD, want="blob missing on seshat")


def test_precheck_rejects_a_core_count_that_disagrees_with_the_inventory(env, capsys):
    env.ports.live_hw["seshat"] = {"cores": 8, "ram_gb": 62}
    _refused(env, capsys, *ADD, want="hardware mismatch on seshat")


def test_precheck_rejects_an_unreachable_node(env, capsys):
    env.ports.live_hw["seshat"] = {"cores": None, "ram_gb": None}
    _refused(env, capsys, *ADD, want="did not answer")


def test_precheck_rejects_a_node_that_is_already_live(env, capsys):
    _refused(env, capsys, "hathor", "--family", "hathor-llama8b", want="already live")


# --- dry run --------------------------------------------------------------------------------------

@pytest.mark.parametrize("flags", [(), ("--yes", "--dry-run")])
def test_dry_run_prints_five_diffs_and_writes_nothing(env, capsys, flags):
    before = env.tracked()
    assert env.cli("add", *ADD, *flags) == 0
    out = capsys.readouterr().out
    for n in range(1, 6):
        assert f"== {n}/5 " in out
    assert "qwen36-local.seshat" in out          # dispatcher/config.json + models.yaml
    assert "127.0.0.1:" in out                   # the out-of-git gateway deployment
    assert "@@" in out and "+  - id: qwen36-local.seshat" in out
    assert env.tracked() == before
    assert env.ports.calls == [] and env.ports.states == []
    assert not os.path.exists(env.state_dir)


# --- full run -------------------------------------------------------------------------------------

def test_full_run_fills_the_bench_fields_and_goes_live(env, capsys):
    assert env.cli("add", *ADD, "--yes") == 0
    out = capsys.readouterr().out
    assert "-> GO" in out and "OK seshat is live" in out
    row = env.reg()["nodes"]["seshat"]
    m = row["models"][0]
    assert (row["state"], row["plug_id"], row["tailscale_ip"]) == ("live", "P6", "127.0.0.1")
    assert (m["prefill_tps"], m["decode_tps"], m["served_ctx"], m["load_s"]) == (62.0, 10.5, 32768, 41.2)
    assert m["first_frame_s"] == math.ceil(32768 / 62.0 * 2)
    assert m["id"] == "qwen36-local.seshat" and m["measured_at"]
    # mirrored to lumen_state as NODE.FAMILY_bench
    (subject, pred, value), = env.ports.states
    assert (subject, pred) == ("seshat", "qwen36_bench") and "GO" in value and "62.0" in value
    # all five targets carry it
    cfg = json.loads(env.read("dispatcher/config.json"))["local_backends"]
    assert cfg["qwen36-local.seshat"]["slots"] == 2 and "qwen36-local.seshat" in cfg["local-worker"]["group"]
    assert "id: qwen36-local.seshat" in env.read("research/models.yaml")
    assert "127.0.0.1:" in open(env.litellm).read()
    assert "qwen36-local" in env.ports.key and "local-worker" in env.ports.key
    assert env.ports.names().count("serve") == 1 and "restart_gateway" in env.ports.names()
    assert env.ports.quarantined == [("qwen36-local.seshat", True)]


def test_full_run_leaves_the_repo_in_render_check_clean_state(env):
    assert env.cli("add", *ADD, "--yes") == 0
    import node_render as nr
    assert nr.drift(env.reg(), env.root) == {}


def test_bench_go_nogo_gate_is_the_qwen36_doc_gate():
    gate = nl.bench_verdict
    fam = {}
    assert gate(GOOD_BENCH, fam) == (True, [])
    assert not gate({**GOOD_BENCH, "prefill_tps": 49.9}, fam)[0]     # QWEN36_LOCAL_WORKER_SERVING.md:117
    assert not gate({**GOOD_BENCH, "decode_tps": 8.9}, fam)[0]
    assert not gate({**GOOD_BENCH, "concurrent_ok": False}, fam)[0]
    assert not gate({**GOOD_BENCH, "oom": True}, fam)[0]
    assert not gate({**GOOD_BENCH, "served_ctx": None}, fam)[0]
    assert gate({**GOOD_BENCH, "prefill_tps": 10.0}, {"bench_gate": {"min_prefill_tps": 8}})[0]


def test_a_bench_that_misses_the_gate_leaves_the_node_down_and_is_not_a_failure(env, capsys):
    env.ports.bench_result = {**GOOD_BENCH, "prefill_tps": 20.0, "decode_tps": 3.1}
    gateway_before = open(env.litellm).read()
    key_before = list(env.ports.key)
    assert env.cli("add", *ADD, "--yes") == 0                        # NO-GO is a result, exit 0
    out = capsys.readouterr().out
    assert "NO-GO" in out and "warm prefill 20.0" in out
    row = env.reg()["nodes"]["seshat"]
    assert row["state"] == "down"                                     # never marked live
    assert (row["models"][0]["prefill_tps"], row["models"][0]["decode_tps"]) == (20.0, 3.1)   # numbers recorded
    assert "NO-GO" in env.ports.states[0][2]
    # no traffic is routed to it: the live gateway and the key allowlist are untouched
    assert open(env.litellm).read() == gateway_before and env.ports.key == key_before
    assert "restart_gateway" not in env.ports.names()
    # and the unit is stopped and the backend quarantined so a passing probe cannot admit work
    assert "unserve" in env.ports.names() and env.ports.quarantined == [("qwen36-local.seshat", False)]
    assert env.reg()["nodes"]["seshat"]["state"] == "down"


# --- failure and rollback -------------------------------------------------------------------------

@pytest.mark.parametrize("break_it", ["gateway", "key"])
def test_a_phase_6_failure_restores_all_five_targets_to_pre_run_bytes(env, capsys, break_it):
    before = env.tracked()
    if break_it == "gateway":
        env.ports.gateway_ok = False
    else:
        env.ports.fail_key_apply = True
    assert env.cli("add", *ADD, "--yes") == 1
    out = capsys.readouterr().out
    assert "FAIL phase 6" in out and "rolled back" in out
    assert env.tracked() == before
    assert "unserve" in env.ports.names()
    assert not os.path.exists(os.path.join(env.root, nl.BLOCK_REL))   # a file the run created is removed again


def test_a_dead_backend_stops_the_unit_and_changes_no_target(env, capsys):
    before = env.tracked()
    env.ports.model_is_ready = False                                  # health never passes within 300 s
    assert env.cli("add", *ADD, "--yes") == 1
    assert "health probe did not pass within 300s" in capsys.readouterr().out
    assert env.tracked() == before and "unserve" in env.ports.names() and env.ports.now >= 300


def test_a_failed_completion_stops_the_unit(env, capsys):
    before = env.tracked()
    env.ports.completes = False
    assert env.cli("add", *ADD, "--yes") == 1
    assert env.tracked() == before and "unserve" in env.ports.names()


def test_rollback_flag_undoes_a_completed_add(env, capsys):
    before = env.tracked()
    assert env.cli("add", *ADD, "--yes") == 0
    assert env.tracked() != before
    assert env.cli("add", *ADD, "--rollback") == 0
    assert env.tracked() == before
    assert env.ports.names().count("unserve") == 1


def test_rollback_with_no_saved_run_is_an_error(env, capsys):
    assert env.cli("add", *ADD, "--rollback") == 1
    assert "no saved add run" in capsys.readouterr().out


# --- reviving a down / retired row ----------------------------------------------------------------

def test_re_adding_the_down_isis_row_keeps_its_capability_history(env, capsys):
    env.ports.bench_result = {**GOOD_BENCH, "prefill_tps": 63.0, "served_ctx": 8192}
    env.healthy("isis")
    assert env.cli("add", "isis", "--family", "qwen36-local", "--slots", "3", "--yes") == 0
    row = env.reg()["nodes"]["isis"]
    assert row["state"] == "live" and row["models"][0]["slots"] == 3
    assert row["models"][0]["capabilities"]["mechanical-edit"]["trusted"] is True
    assert row["plug_id"] == "p1"


# --- registry text edits keep the audit trail -----------------------------------------------------

def test_set_field_keeps_comments_and_touches_only_the_named_row():
    text = open(os.path.join(os.path.dirname(nl.HERE), "research", "inference_nodes.yaml")).read()
    new = ed.set_field(text, "isis", "state", "retired")
    new = ed.set_field(new, "isis", "prefill_tps", 71.5, family="qwen36-local")
    assert yaml.safe_load(new)["nodes"]["isis"]["state"] == "retired"
    assert yaml.safe_load(new)["nodes"]["isis"]["models"][0]["prefill_tps"] == 71.5
    assert "out of service, wq#2140" in new and "71.5            # bench 63" in new
    assert yaml.safe_load(new)["nodes"]["hathor"] == yaml.safe_load(text)["nodes"]["hathor"]
    assert new.count("\n") == text.count("\n")
