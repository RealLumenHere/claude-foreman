import pathlib
import sys

import pytest
import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import swap_vision_model as m  # noqa: E402

FIXTURE = (HERE / "fixtures" / "litellm_config.yaml").read_text()


def test_check_reports_needs_apply_on_moondream():
    assert m.do_check(FIXTURE, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b") == 1


def test_check_passes_once_swapped():
    new_text, _ = m.build(FIXTURE, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")
    assert m.do_check(new_text, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b") == 0


def test_build_swaps_only_the_model_value():
    new_text, changed = m.build(FIXTURE, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")
    assert changed is True
    after = yaml.safe_load(new_text)
    entry = next(e for e in after["model_list"] if e["model_name"] == "lumen-vision")
    assert entry["litellm_params"]["model"] == "ollama/qwen3-vl:8b"
    assert entry["model_info"]["supports_vision"] is True
    assert "ollama/vision" not in new_text


def test_build_preserves_comment_and_other_entries():
    new_text, _ = m.build(FIXTURE, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")
    assert "# lumen-vision: moondream" in new_text
    after = yaml.safe_load(new_text)
    assert {e["model_name"] for e in after["model_list"]} == {
        "lumen-lfm2-1b", "deepseek-v4-pro", "lumen-vision", "lumen/judge",
    }
    assert after["router_settings"]["model_group_alias"]["lumen/vision"] == "lumen-vision"


def test_apply_is_idempotent_second_pass_is_a_noop():
    new_text, changed = m.build(FIXTURE, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")
    new_text2, changed2 = m.build(new_text, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")
    assert changed2 is False
    assert new_text2 == new_text


def test_apply_refuses_to_write_if_render_would_not_match_plan(monkeypatch):
    real_plan = m.plan

    def bad_plan(config, group, to_model):
        want = real_plan(config, group, to_model)
        want["model_list"].append({"model_name": "injected-by-bug"})
        return want

    monkeypatch.setattr(m, "plan", bad_plan)
    with pytest.raises(RuntimeError, match="does not match the expected plan"):
        m.build(FIXTURE, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")


def test_write_backs_up_before_editing(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(FIXTURE)
    rc = m.main(["apply", str(cfg), "--write"])
    assert rc == 0
    backups = list(tmp_path.glob("litellm_config.yaml.bak-wq4423-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == FIXTURE
    assert m.do_check(cfg.read_text(), "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b") == 0


def test_refuses_on_drifted_model_value_outside_from_or_to():
    # ACCEPTANCE PROBE outside the from/to population (wq#2630): a config
    # whose lumen-vision deployment already points somewhere else entirely
    # (neither the old moondream tag nor the new qwen3-vl tag) must stop,
    # not silently overwrite it as if it were still on moondream.
    drifted = FIXTURE.replace("model: ollama/vision", "model: ollama/some-other-tag")
    with pytest.raises(RuntimeError, match="refusing to guess"):
        m.build(drifted, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")
    assert m.do_check(drifted, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b") == 2


def test_refuses_on_ambiguous_duplicate_group():
    dup = FIXTURE.replace(
        "- model_name: lumen/judge",
        "- model_name: lumen-vision\n  litellm_params:\n    model: ollama/vision-2\n"
        "- model_name: lumen/judge",
    )
    with pytest.raises(RuntimeError, match="ambiguous"):
        m.build(dup, "lumen-vision", "ollama/vision", "ollama/qwen3-vl:8b")


def test_missing_group_is_a_hard_error():
    with pytest.raises(RuntimeError, match="not found"):
        m.build(FIXTURE, "no-such-group", "ollama/vision", "ollama/qwen3-vl:8b")
