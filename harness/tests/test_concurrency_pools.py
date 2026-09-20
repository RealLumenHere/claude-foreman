"""concurrency.py pool classes (wq#2130): LOCAL_POOL_MODELS comes from research/models.yaml's
`local` pool; deepseek-v4-flash is its own cheap_cloud class — still exempt from every
Anthropic gate, but not a Mac slot.
"""
import os
import sys

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import concurrency  # noqa: E402


def write_models(tmp_path, rows):
    p = tmp_path / "models.yaml"
    p.write_text("models:\n" + "".join(f"  - {{id: {i}, pools: [{pool}]}}\n" for i, pool in rows))
    return str(p)


def test_local_pool_is_read_from_models_yaml(tmp_path):
    p = write_models(tmp_path, [("box-a", "local"), ("box-b", "local"),
                                ("vendor-x", "cheap_cloud"), ("claude-x", "session")])
    assert concurrency._pool_models("local", ("fallback",), p) == ("box-a", "box-b")
    assert concurrency._pool_models("cheap_cloud", ("fallback",), p) == ("vendor-x",)


def test_pool_falls_back_when_yaml_is_unusable(tmp_path):
    assert concurrency._pool_models("local", ("fb",), str(tmp_path / "missing.yaml")) == ("fb",)
    bad = tmp_path / "bad.yaml"
    bad.write_text("models: [unclosed")
    assert concurrency._pool_models("local", ("fb",), str(bad)) == ("fb",)


def test_live_models_yaml_local_pool_is_the_three_macs_plus_group():
    assert set(concurrency.LOCAL_POOL_MODELS) == {
        "qwen36-local", "nephthys-qwen14b", "hathor-llama8b", "local-worker"}
    assert concurrency.CHEAP_CLOUD_MODELS == ("deepseek-v4-flash",)
    assert "deepseek-v4-flash" not in concurrency.LOCAL_POOL_MODELS


def test_deepseek_is_exempt_but_not_a_mac_slot():
    assert concurrency.is_cheap_cloud_model("deepseek-v4-flash")
    assert concurrency.is_local_model("deepseek-v4-flash")          # Anthropic-exempt
    assert not concurrency.is_mac_local_model("deepseek-v4-flash")  # no Mac slot
    assert "deepseek-v4-flash" in concurrency.ANTHROPIC_EXEMPT_MODELS


def test_mac_models_are_local_and_cloud_models_are_neither():
    for m in ("hathor-llama8b", "nephthys-qwen14b", "qwen36-local", "local-worker",
              "local-worker@local"):
        assert concurrency.is_mac_local_model(m) and concurrency.is_local_model(m)
    for m in ("claude-sonnet-5", "claude-haiku-4-5", None, ""):
        assert not concurrency.is_local_model(m) and not concurrency.is_mac_local_model(m)
        assert not concurrency.is_cheap_cloud_model(m)


def test_deepseek_unit_is_still_excluded_from_the_cloud_count():
    lookup = lambda tid: {"1": "deepseek-v4-flash", "2": "claude-sonnet-5"}.get(tid)  # noqa: E731
    assert concurrency.is_local_unit("fworker-wq1", lookup) is True
    assert concurrency.is_local_unit("fworker-wq2", lookup) is False


def test_observed_burn_still_treats_deepseek_as_non_anthropic():
    sys.path.insert(0, os.path.join(os.path.dirname(HARNESS_DIR), "dispatcher"))
    import observed_burn_core
    assert observed_burn_core.is_local_ticket("deepseek-v4-flash")
    assert observed_burn_core.is_local_ticket("hathor-llama8b")
    assert not observed_burn_core.is_local_ticket("claude-sonnet-5")
