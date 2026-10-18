import pathlib
import sys

import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import dedupe_qwen_local_small as m  # noqa: E402

FIXTURE = (HERE / "fixtures" / "litellm_config.yaml").read_text()


def test_check_fails_on_duplicate_with_no_fallback():
    assert m.do_check(FIXTURE, "qwen-local-small") == 1


def test_check_passes_once_fixed():
    new_text, _, _ = m.build(FIXTURE, "qwen-local-small", "192.168.8.135", "lumen-local-judge")
    assert m.do_check(new_text, "qwen-local-small") == 0


def test_build_drops_only_the_dead_host_deployment():
    new_text, removed, added = m.build(FIXTURE, "qwen-local-small", "192.168.8.135", "lumen-local-judge")
    assert removed == 1
    assert added is True
    after = yaml.safe_load(new_text)
    entries = [e for e in after["model_list"] if e["model_name"] == "qwen-local-small"]
    assert len(entries) == 1
    assert "192.168.8.125" in entries[0]["litellm_params"]["api_base"]
    assert "192.168.8.135" not in new_text


def test_build_preserves_the_group_comment_and_other_entries():
    new_text, _, _ = m.build(FIXTURE, "qwen-local-small", "192.168.8.135", "lumen-local-judge")
    assert "# qwen-local-small: two deployments" in new_text
    after = yaml.safe_load(new_text)
    assert {e["model_name"] for e in after["model_list"]} == {
        "lumen-lfm2-1b", "deepseek-v4-pro", "qwen-local-small",
        "lumen/judge", "lumen-local-judge",
    }


def test_build_adds_fallback_without_disturbing_existing_ones():
    new_text, _, _ = m.build(FIXTURE, "qwen-local-small", "192.168.8.135", "lumen-local-judge")
    after = yaml.safe_load(new_text)
    fb = {k: v for e in after["router_settings"]["fallbacks"] for k, v in e.items()}
    assert fb["qwen-local-small"] == ["lumen-local-judge"]
    assert fb["lumen/judge"] == ["lumen-local-judge"]
    assert fb["lumen-lfm2-1b"] == ["openrouter-free"]


def test_apply_is_idempotent_second_pass_is_a_noop():
    new_text, _, _ = m.build(FIXTURE, "qwen-local-small", "192.168.8.135", "lumen-local-judge")
    new_text2, removed2, added2 = m.build(new_text, "qwen-local-small", "192.168.8.135", "lumen-local-judge")
    assert removed2 == 0
    assert added2 is False
    assert new_text2 == new_text


def test_apply_refuses_to_write_if_render_would_not_match_plan(monkeypatch):
    real_plan = m.plan

    def bad_plan(config, group, drop_host, to):
        want = real_plan(config, group, drop_host, to)
        want["model_list"].append({"model_name": "injected-by-bug"})
        return want

    monkeypatch.setattr(m, "plan", bad_plan)
    try:
        m.build(FIXTURE, "qwen-local-small", "192.168.8.135", "lumen-local-judge")
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "does not match the expected plan" in str(e)


def test_write_backs_up_before_editing(tmp_path):
    cfg = tmp_path / "litellm_config.yaml"
    cfg.write_text(FIXTURE)
    rc = m.main(["apply", str(cfg), "--write"])
    assert rc == 0
    backups = list(tmp_path.glob("litellm_config.yaml.bak-wq4213-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == FIXTURE
    assert m.do_check(cfg.read_text(), "qwen-local-small") == 0
