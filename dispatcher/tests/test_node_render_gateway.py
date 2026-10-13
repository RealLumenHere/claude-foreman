"""wq#2251: the two gateway-side generators (litellm block + scoped key allowlist), their apply and
rollback. Design research/INFERENCE_NODE_REGISTRY.md §3 rows 1 and 4; acceptance T2 (§8)."""
import copy
import json
import os
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "harness"))
import inference_registry as ir  # noqa: E402
import node_render_gateway as g  # noqa: E402

COMMITTED_BLOCK = os.path.join(REPO, "ops", "inference-registry", "litellm_local_block.yaml")

HAND_CONFIG = """\
model_list:
- model_name: gemma4-e4b-local
  litellm_params:
    model: ollama/gemma
    api_base: http://host.docker.internal:11434
  model_info:
    mode: chat
- model_name: nephthys-qwen14b   # hand-written, will be adopted
  litellm_params:
    model: openai/qwen3:14b
    api_base: http://100.98.74.87:11434/v1
    max_parallel_requests: 2
  model_info: {mode: chat}
- model_name: hathor-llama8b
  litellm_params:
    model: openai/llama3.1:8b
    api_base: http://100.80.71.13:11434/v1
  model_info: {mode: chat}
- model_name: qwen36-live   # unowned: must survive adoption
  litellm_params:
    model: openai/qwen3.6-35b-a3b
    api_base: http://100.100.129.24:8095/v1
  model_info:
    mode: chat
- model_name: local-worker
  litellm_params:
    model: openai/llama3.1:8b
    api_base: http://100.80.71.13:11434/v1
  model_info: {mode: chat, id: local-worker-hathor}
- model_name: deepseek-v4-flash
  litellm_params:
    model: deepseek/deepseek-v4-flash
    api_key: os.environ/DEEPSEEK_API_KEY
  model_info:
    mode: chat
litellm_settings:
  drop_params: true
"""


_head, _tail = HAND_CONFIG.split("litellm_settings:")
_items = _head.split("model_list:\n")[1]
INDENTED_CONFIG = "model_list:\n" + "".join(f"  {ln}\n" for ln in _items.splitlines()) + "litellm_settings:" + _tail


@pytest.fixture
def reg():
    return copy.deepcopy(ir.load())


@pytest.fixture
def icfg(tmp_path):
    d = tmp_path / "agent"
    d.mkdir()
    p = d / "litellm_config.yaml"
    p.write_text(INDENTED_CONFIG)
    return str(p)


@pytest.fixture
def cfg(tmp_path):
    d = tmp_path / "agent"
    d.mkdir()
    p = d / "litellm_config.yaml"
    p.write_text(HAND_CONFIG)
    return str(p)


def names(text):
    return [m["model_name"] for m in yaml.safe_load(text)["model_list"]]


def block_items(reg):
    body = g.render_litellm(reg).splitlines()[1:-1]
    return yaml.safe_load("\n".join(body)) or []


def backups(path):
    return [f for f in os.listdir(os.path.dirname(path)) if ".bak-" in f]


# --- render -------------------------------------------------------------------------------

def test_render_is_deterministic_and_sentinel_wrapped(reg):
    a = g.render_litellm(reg)
    assert a == g.render_litellm(copy.deepcopy(reg))
    assert a.startswith(g.BEGIN + "\n") and a.endswith(g.END + "\n")


def test_render_pins_then_group_one_deployment_per_served_model(reg):
    # wq#2796: hathor is dedicated to the judge (local_worker_group: false) and does not join
    # the local-worker group, even though it still gets its own pin below.
    items = block_items(reg)
    assert [i["model_name"] for i in items] == [
        "hathor-llama8b", "nephthys-qwen14b", "qwen36-local", "local-worker", "local-worker"]
    grp = [i for i in items if i["model_name"] == "local-worker"]
    assert [i["model_info"]["id"] for i in grp] == ["local-worker-nephthys", "local-worker-isis"]
    nephthys = grp[0]
    assert nephthys["litellm_params"]["api_base"] == "http://100.98.74.87:11434/v1"
    assert nephthys["litellm_params"]["model"] == "openai/qwen3:14b"
    assert nephthys["litellm_params"]["max_parallel_requests"] == 2
    assert nephthys["litellm_params"]["timeout"] == 2400 + g.TIMEOUT_MARGIN_S
    assert nephthys["model_info"]["max_input_tokens"] == 32768 - g.OUTPUT_HEADROOM


def test_hathor_dedicated_out_of_group_but_keeps_its_pin(reg):
    """wq#2796: batch/fworker traffic through the `local-worker` alias must not be able to land
    on hathor's ollama slots and contend with a synchronous judge call (research/RISK_TIERED_
    JUDGE.md §4). Falsifiable: flipping the flag back to True must restore the old 3-member group."""
    items = block_items(reg)
    pins = [i for i in items if i["model_name"] == "hathor-llama8b"]
    assert len(pins) == 1  # still individually addressable, e.g. by bin/judge's JUDGE_MODEL_PIN
    group_ids = [i["model_info"]["id"] for i in items if i["model_name"] == "local-worker"]
    assert "local-worker-hathor" not in group_ids

    reg["nodes"]["hathor"]["models"][0]["local_worker_group"] = True
    restored = [i["model_info"]["id"] for i in block_items(reg) if i["model_name"] == "local-worker"]
    assert "local-worker-hathor" in restored


def test_render_carries_registry_family_options(reg):
    # wq#3794: isis's family blob moved from Qwen3.6-35B-A3B to qwen2.5-coder:14b (MoE prefill
    # off no-AVX2 CPU); hathor's judge blob moved to llama3.1:8b-instruct-q8_0.
    by = {i["litellm_params"]["model"]: i for i in block_items(reg)}
    assert by["openai/qwen25-coder-14b"]["litellm_params"]["api_base"] == "http://100.100.129.24:8095/v1"
    assert by["openai/qwen3:14b"]["litellm_params"]["extra_body"] == {"reasoning": {"effort": "none"}}
    assert "extra_body" not in by["openai/llama3.1:8b-instruct-q8_0"]["litellm_params"]


def test_committed_block_matches_registry_render(reg):
    with open(COMMITTED_BLOCK) as f:
        assert f.read() == g.render_litellm(reg)


def test_key_allowlist_is_group_plus_served_pins(reg):
    assert g.render_key_allowlist(reg) == ["local-worker", "hathor-llama8b", "nephthys-qwen14b", "qwen36-local"]


def test_deleting_a_node_drops_it_from_every_gateway_render(reg):
    del reg["nodes"]["nephthys"]
    block = g.render_litellm(reg)
    assert "nephthys" not in block and "100.98.74.87" not in block
    assert g.render_key_allowlist(reg) == ["local-worker", "hathor-llama8b", "qwen36-local"]


def test_retired_state_drops_but_down_stays(reg):
    reg["nodes"]["nephthys"]["state"] = "retired"
    assert "nephthys" not in g.render_litellm(reg)
    assert "100.100.129.24" in g.render_litellm(reg)  # isis is `down`, keeps its deployment


def test_no_served_nodes_renders_empty_block_and_no_group(reg):
    for n in reg["nodes"].values():
        n["state"] = "retired"
    assert block_items(reg) == []
    assert g.render_key_allowlist(reg) == []


# --- litellm apply / rollback ---------------------------------------------------------------

def test_first_apply_adopts_hand_written_items_and_keeps_neighbours(reg, cfg):
    assert g.apply_litellm(reg, cfg)["changed"]
    text = open(cfg).read()
    assert names(text) == [
        "gemma4-e4b-local", "hathor-llama8b", "nephthys-qwen14b", "qwen36-local",
        "local-worker", "local-worker", "qwen36-live", "deepseek-v4-flash"]
    assert "hand-written, will be adopted" not in text
    assert "qwen36-live   # unowned: must survive adoption" in text
    assert yaml.safe_load(text)["litellm_settings"] == {"drop_params": True}
    assert text.count(g.BEGIN) == 1 and text.count(g.END) == 1


def test_backup_is_taken_first_and_equals_the_original(reg, cfg):
    res = g.apply_litellm(reg, cfg)
    assert res["backup"].startswith(cfg + ".bak-")
    assert open(res["backup"]).read() == HAND_CONFIG


def test_apply_twice_second_run_is_a_no_op(reg, cfg):
    g.apply_litellm(reg, cfg)
    once = open(cfg).read()
    assert g.apply_litellm(reg, cfg) == {"changed": False, "backup": None}
    assert open(cfg).read() == once
    assert len(backups(cfg)) == 1


def test_retire_by_rerender_then_restore_is_the_rollback(reg, cfg):
    g.apply_litellm(reg, cfg)
    full = open(cfg).read()
    smaller = copy.deepcopy(reg)
    del smaller["nodes"]["nephthys"]
    assert g.apply_litellm(smaller, cfg)["changed"]
    assert "100.98.74.87" not in open(cfg).read()
    assert g.apply_litellm(reg, cfg)["changed"]
    assert open(cfg).read() == full


def test_rollback_restores_newest_backup_and_saves_current(reg, cfg):
    g.apply_litellm(reg, cfg)
    generated = open(cfg).read()
    res = g.rollback_litellm(cfg)
    assert open(cfg).read() == HAND_CONFIG
    assert open(res["saved_current"]).read() == generated


def test_rollback_without_backup_errors(cfg):
    with pytest.raises(g.GatewayError, match="no litellm_config.yaml.bak"):
        g.rollback_litellm(cfg)


def test_owned_name_outside_sentinels_is_refused(reg, cfg):
    g.apply_litellm(reg, cfg)
    text = open(cfg).read().replace(
        "litellm_settings:", "- model_name: hathor-llama8b\n  litellm_params: {model: x}\nlitellm_settings:")
    open(cfg, "w").write(text)
    with pytest.raises(g.GatewayError, match="also defined outside the block"):
        g.apply_litellm(reg, cfg)
    assert open(cfg).read() == text


ADOPTED_NAMES = [
    "gemma4-e4b-local", "hathor-llama8b", "nephthys-qwen14b", "qwen36-local",
    "local-worker", "local-worker", "qwen36-live", "deepseek-v4-flash"]


def test_indented_first_apply_adopts_and_reindents_block(reg, icfg):
    assert g.apply_litellm(reg, icfg)["changed"]
    text = open(icfg).read()
    assert names(text) == ADOPTED_NAMES
    assert "hand-written, will be adopted" not in text
    assert "  - model_name: qwen36-live   # unowned: must survive adoption" in text
    assert f"  {g.BEGIN}\n" in text and f"  {g.END}\n" in text
    assert yaml.safe_load(text)["litellm_settings"] == {"drop_params": True}
    assert all(ln.startswith("  ") for ln in text.split("litellm_settings:")[0].splitlines()[1:])


def test_indented_sentinel_replace_is_idempotent_and_tracks_registry(reg, icfg):
    g.apply_litellm(reg, icfg)
    once = open(icfg).read()
    assert g.apply_litellm(reg, icfg) == {"changed": False, "backup": None}
    smaller = copy.deepcopy(reg)
    del smaller["nodes"]["nephthys"]
    assert g.apply_litellm(smaller, icfg)["changed"]
    assert "100.98.74.87" not in open(icfg).read()
    assert g.apply_litellm(reg, icfg)["changed"]
    assert open(icfg).read() == once


def test_indented_owned_name_outside_sentinels_is_refused(reg, icfg):
    g.apply_litellm(reg, icfg)
    text = open(icfg).read().replace(
        "litellm_settings:", "  - model_name: hathor-llama8b\n    litellm_params: {model: x}\nlitellm_settings:")
    open(icfg, "w").write(text)
    with pytest.raises(g.GatewayError, match="also defined outside the block"):
        g.apply_litellm(reg, icfg)
    assert open(icfg).read() == text


def test_committed_block_stays_column_zero(reg):
    assert g.render_litellm(reg).splitlines()[1].startswith("- model_name:")


def test_unbalanced_sentinels_are_refused(reg, cfg):
    g.apply_litellm(reg, cfg)
    text = open(cfg).read().replace(g.END + "\n", "")
    open(cfg, "w").write(text)
    with pytest.raises(g.GatewayError, match="unbalanced"):
        g.apply_litellm(reg, cfg)
    assert open(cfg).read() == text


def test_config_without_model_list_is_refused(reg, tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("general_settings: {}\n")
    with pytest.raises(g.GatewayError, match="model_list"):
        g.apply_litellm(reg, str(p))


def test_write_preserves_file_mode(reg, cfg):
    os.chmod(cfg, 0o640)
    g.apply_litellm(reg, cfg)
    assert os.stat(cfg).st_mode & 0o777 == 0o640


def test_restart_is_a_detached_transient_unit():
    seen = {}

    class P:
        returncode, stderr = 0, ""

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        return P()

    unit = g.restart_gateway("litellm", runner=runner, stamp="1")
    cmd = seen["cmd"]
    assert cmd[:3] == ["sudo", "-n", "systemd-run"]
    assert "--no-block" in cmd and f"--unit={unit}" in cmd
    assert cmd[-3:] == ["docker", "restart", "litellm"]
    assert "--wait" not in cmd and "--pipe" not in cmd


def test_restart_failure_raises():
    class P:
        returncode, stderr = 1, "no polkit"

    with pytest.raises(g.GatewayError, match="no polkit"):
        g.restart_gateway("litellm", runner=lambda *a, **k: P())


# --- key allowlist --------------------------------------------------------------------------

SCOPED = "sk-" + "scoped-SECRET-1234"
ADMIN = "sk-" + "admin-SECRET-9876"


class FakeGateway:
    def __init__(self, models, update_status=200):
        self.models, self.update_status, self.calls = list(models), update_status, []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, dict(headers), body))
        if url.endswith("/key/info"):
            return 200, {"info": {"models": list(self.models)}}
        if url.endswith("/key/update"):
            if self.update_status == 200:
                self.models = list(body["models"])
            return self.update_status, {}
        raise AssertionError(url)


@pytest.fixture
def keys(tmp_path):
    k, a = tmp_path / "scoped.token", tmp_path / "admin.token"
    k.write_text(SCOPED + "\n")
    a.write_text(ADMIN + "\n")
    for f in (k, a):
        f.chmod(0o600)
    bdir = tmp_path / "bak"
    bdir.mkdir()
    return str(k), str(a), str(bdir)


def apply_key(reg, keys, gw, admin=True):
    return g.apply_key_allowlist(reg, keys[0], keys[1] if admin else None, "http://gw:4000/", gw, keys[2])


def test_key_merge_keeps_foreign_names_and_swaps_owned(reg):
    cur = ["deepseek-v4-flash", "qwen36-local", "old-node-pin"]
    assert g.merge_allowlist(cur, reg) == [
        "deepseek-v4-flash", "old-node-pin", "local-worker", "hathor-llama8b", "nephthys-qwen14b",
        "qwen36-local"]


def test_key_apply_updates_with_admin_key_and_secret_stays_out_of_urls(reg, keys):
    gw = FakeGateway(["deepseek-v4-flash", "qwen36-local"])
    res = apply_key(reg, keys, gw)
    assert res["changed"] and gw.models == res["after"]
    method, url, headers, body = gw.calls[-1]
    assert (method, url) == ("POST", "http://gw:4000/key/update")
    assert headers["Authorization"] == f"Bearer {ADMIN}"
    assert body["key"] == SCOPED and body["models"] == res["after"]
    assert all(SCOPED not in u and ADMIN not in u for _, u, _, _ in gw.calls)
    assert json.load(open(res["backup"])) == ["deepseek-v4-flash", "qwen36-local"]
    assert SCOPED not in open(res["backup"]).read()


def test_key_apply_twice_second_run_makes_no_update(reg, keys):
    gw = FakeGateway(["deepseek-v4-flash"])
    apply_key(reg, keys, gw)
    n = len(gw.calls)
    assert apply_key(reg, keys, gw)["changed"] is False
    assert [c[0] for c in gw.calls[n:]] == ["GET"]
    assert len(os.listdir(keys[2])) == 1


def test_key_retire_drops_the_nodes_name_from_the_allowlist(reg, keys):
    gw = FakeGateway(["deepseek-v4-flash"])
    apply_key(reg, keys, gw)
    del reg["nodes"]["nephthys"]
    assert apply_key(reg, keys, gw)["changed"]
    assert "nephthys-qwen14b" not in gw.models and "hathor-llama8b" in gw.models


def test_key_rollback_restores_prior_list(reg, keys):
    gw = FakeGateway(["deepseek-v4-flash", "qwen36-local"])
    apply_key(reg, keys, gw)
    g.rollback_key_allowlist(keys[0], keys[1], "http://gw:4000", gw, keys[2])
    assert gw.models == ["deepseek-v4-flash", "qwen36-local"]


def test_key_403_error_names_the_env_var_and_never_the_key(reg, keys):
    with pytest.raises(g.GatewayError) as e:
        apply_key(reg, keys, FakeGateway(["deepseek-v4-flash"], update_status=403))
    assert "FOREMAN_LITELLM_ADMIN_KEY_FILE" in str(e.value)
    assert SCOPED not in str(e.value) and ADMIN not in str(e.value)


def test_key_transport_error_is_masked(reg, keys):
    def boom(method, url, headers, body=None):
        raise g.GatewayError(f"connect failed, sent {headers['Authorization']}")

    with pytest.raises(g.GatewayError) as e:
        apply_key(reg, keys, boom)
    assert SCOPED not in str(e.value) and "<masked>" in str(e.value)


def test_missing_and_empty_key_files_error_without_contents(reg, keys, tmp_path):
    empty = tmp_path / "empty"
    empty.write_text("\n")
    with pytest.raises(g.GatewayError, match="is empty"):
        g.apply_key_allowlist(reg, str(empty), None, "http://gw", FakeGateway([]), keys[2])
    with pytest.raises(g.GatewayError, match="cannot read key file"):
        g.apply_key_allowlist(reg, str(tmp_path / "nope"), None, "http://gw", FakeGateway([]), keys[2])


def test_without_admin_file_the_scoped_key_authorises(reg, keys):
    gw = FakeGateway(["x"])
    apply_key(reg, keys, gw, admin=False)
    assert gw.calls[-1][2]["Authorization"] == f"Bearer {SCOPED}"


# --- CLI ------------------------------------------------------------------------------------

def test_cli_render_writes_then_check_passes_and_detects_drift(tmp_path, capsys):
    out = str(tmp_path / "block.yaml")
    assert g.main(["render", "--block-path", out]) == 0
    assert json.loads(capsys.readouterr().out)["models"][0] == "local-worker"
    assert open(out).read() == g.render_litellm(ir.load())
    assert g.main(["render", "--check", "--block-path", out]) == 0
    open(out, "a").write("# drift\n")
    assert g.main(["render", "--check", "--block-path", out]) == 2


def test_cli_rollback_dry_run_touches_nothing(reg, cfg, capsys, monkeypatch):
    monkeypatch.setattr(g, "LITELLM_CONFIG", cfg)
    g.apply_litellm(reg, cfg)
    generated = open(cfg).read()
    before = sorted(os.listdir(os.path.dirname(cfg)))
    capsys.readouterr()
    assert g.main(["rollback", "--dry-run", "--config", cfg]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["would_restore"].startswith(cfg + ".bak-")
    assert open(cfg).read() == generated
    assert sorted(os.listdir(os.path.dirname(cfg))) == before


def test_cli_dry_run_touches_nothing(cfg, capsys):
    assert g.main(["apply", "--dry-run", "--config", cfg]) == 0
    assert json.loads(capsys.readouterr().out)["config_changes"] is True
    assert open(cfg).read() == HAND_CONFIG
    assert not backups(cfg)
