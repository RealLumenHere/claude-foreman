"""Design §8 T2 for the three in-git targets (the two gateway targets are wq#2251); wq#2250.
Fixtures render into a scratch copy of the repo's target files, never the live tree.
"""
import copy
import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS_DIR))
HARNESS_DIR = os.path.join(REPO, "harness")
sys.path.insert(0, HARNESS_DIR)
import fleet_node  # noqa: E402
import inference_registry as ir  # noqa: E402
import node_render as nr  # noqa: E402

TARGETS = (nr.DISPATCHER_CONFIG, nr.MODELS_YAML) + nr.PREFIX_FILES


@pytest.fixture
def reg():
    return ir.load()


@pytest.fixture
def root(tmp_path):
    for rel in TARGETS:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(REPO, rel), dst)
    return str(tmp_path)


def read(root, rel):
    with open(os.path.join(root, rel)) as f:
        return f.read()


def write(root, rel, text):
    with open(os.path.join(root, rel), "w") as f:
        f.write(text)


def test_render_check_against_the_real_repo_exits_zero():
    """The CI gate: the committed targets are exactly what the registry renders."""
    r = subprocess.run([sys.executable, os.path.join(HARNESS_DIR, "fleet_node.py"), "render", "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_render_reproduces_the_committed_targets_byte_for_byte(reg):
    assert nr.drift(reg, REPO) == {}


def test_apply_twice_produces_no_diff_the_second_time(reg, root):
    write(root, nr.DISPATCHER_CONFIG, read(root, nr.DISPATCHER_CONFIG).replace('"slots": 2', '"slots": 3', 1))
    assert nr.apply(reg, root) == [nr.DISPATCHER_CONFIG]
    snap = {rel: read(root, rel) for rel in TARGETS}
    assert nr.apply(reg, root) == []
    assert {rel: read(root, rel) for rel in TARGETS} == snap


def test_check_exits_2_on_drift_and_names_the_file(root, capsys):
    write(root, nr.DISPATCHER_CONFIG, read(root, nr.DISPATCHER_CONFIG).replace('"slots": 2', '"slots": 3', 1))
    assert fleet_node.main(["render", "--check", "--root", root]) == 2
    assert nr.DISPATCHER_CONFIG in capsys.readouterr().out
    assert fleet_node.main(["render", "--apply", "--root", root]) == 0
    assert fleet_node.main(["render", "--check", "--root", root]) == 0


def test_dispatcher_render_is_the_shape_the_existing_lint_accepts(reg):
    cfg = json.loads(nr.render_dispatcher(reg, REPO))
    lint = nr._dispatcher_lint()
    assert lint.validate_local_backends(cfg) == ([], [])
    rows = cfg["local_backends"]
    assert rows["local-worker"] == {"group": ["hathor-llama8b", "nephthys-qwen14b", "qwen36-local"]}
    assert rows["hathor-llama8b"]["health_url"] == "http://100.80.71.13:11434/api/ps"


def test_dispatcher_render_refuses_a_row_the_lint_rejects(reg, root):
    bad = copy.deepcopy(reg)
    bad["nodes"]["hathor"]["models"][0]["slots"] = 0
    with pytest.raises(nr.RenderError, match="dispatch_config_lint"):
        nr.render_dispatcher(bad, root)


def test_hand_written_neighbours_survive_a_render(reg, root):
    nr.apply(reg, root)
    cfg = json.loads(read(root, nr.DISPATCHER_CONFIG))
    assert cfg["local_backends"]["_note"].startswith("wq#2130")
    assert cfg["lanes"]["build"]["enabled"] is True
    rows = {r["id"]: r for r in yaml.safe_load(read(root, nr.MODELS_YAML))["models"]}
    assert rows["nephthys-qwen3-14b"]["status"] == "RETIRED"
    assert rows["claude-sonnet-5"]["status"] == "LIVE"


def test_models_rows_carry_pool_membership_ctx_and_trust(reg):
    rows = {r["id"]: r for r in yaml.safe_load(nr.render_models(reg, REPO))["models"]}
    for mid in ("hathor-llama8b", "nephthys-qwen14b", "qwen36-local", "local-worker"):
        assert rows[mid]["pools"] == ["local"]
    assert rows["hathor-llama8b"]["context_tokens"] == 131072
    assert rows["qwen36-local"]["status"] == "down"
    assert "standard-engineering" not in rows["qwen36-local"]["trust"]
    assert "standard-engineering" in rows["hathor-llama8b"]["trust"]
    assert rows["local-worker"]["served_ctx"] == 32768   # min over the members that are not down


def test_deleting_a_node_drops_it_from_all_three_targets(reg, root):
    gone = copy.deepcopy(reg)
    del gone["nodes"]["isis"]
    cfg = json.loads(nr.render_dispatcher(gone, root))["local_backends"]
    assert "qwen36-local" not in cfg
    assert cfg["local-worker"]["group"] == ["hathor-llama8b", "nephthys-qwen14b"]
    ids = [r["id"] for r in yaml.safe_load(nr.render_models(gone, root))["models"]]
    assert "qwen36-local" not in ids and "local-worker" in ids
    # the frozen governor prefixes are not node-owned: dropping a box never drops a prefix
    for rel, text in nr.render_prefix_lists(gone, root).items():
        assert '"qwen36-local"' in text, rel


def test_retired_node_is_omitted_but_down_node_keeps_its_row(reg):
    down = copy.deepcopy(reg)
    assert "qwen36-local" in json.loads(nr.render_dispatcher(down, REPO))["local_backends"]
    down["nodes"]["isis"]["state"] = "retired"
    assert "qwen36-local" not in json.loads(nr.render_dispatcher(down, REPO))["local_backends"]


def test_prefix_tuples_follow_the_registry_and_stay_literal(reg, root):
    changed = copy.deepcopy(reg)
    changed["governor_prefixes"] = ["deepseek-v4-flash", "hathor-llama8b"]
    for rel, text in nr.render_prefix_lists(changed, root).items():
        assert 'LOCAL_POOL_MODELS = ("deepseek-v4-flash", "hathor-llama8b")' in text.splitlines(), rel
    assert nr.prefix_literal(["only"]) == 'LOCAL_POOL_MODELS = ("only",)'


def test_missing_sentinel_is_an_error_not_a_silent_skip(reg, root):
    write(root, "despag/app.py", read(root, "despag/app.py").replace(nr.BEGIN, "BEGIN removed"))
    assert fleet_node.main(["render", "--check", "--root", root]) == 1


def test_a_shared_model_id_on_two_nodes_is_refused(reg):
    bad = copy.deepcopy(reg)
    bad["nodes"]["nephthys"]["models"][0]["family"] = "hathor-llama8b"
    with pytest.raises(nr.RenderError, match="served by both"):
        nr.backends(bad)
