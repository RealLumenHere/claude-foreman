"""wq#4101 — fix_think_rungs against the committed fixture (never the live config)."""
import pathlib
import sys

import pytest
import yaml

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import fix_think_rungs as ftr  # noqa: E402

FIXTURE = HERE / "fixture_config.yaml"


@pytest.fixture()
def text():
    return FIXTURE.read_text()


@pytest.fixture()
def cfg(text):
    return yaml.safe_load(text)


def _params(cfg, name):
    return next(e for e in cfg["model_list"] if e["model_name"] == name)["litellm_params"]


def test_classify_buckets(cfg):
    plan = ftr.classify(cfg)
    assert sorted(plan["fix"]) == ["lumen/chat-exp", "lumen/code"]
    assert plan["hand_merge"] == ["lumen/handmerge"]
    assert plan["suspects"] == ["lumen/misc"]


def test_fix_text_matches_expected_parse(text, cfg):
    plan = ftr.classify(cfg)
    after = yaml.safe_load(ftr.fix_text(text, set(plan["fix"])))
    assert after == ftr.expected_after(cfg, set(plan["fix"]))


def test_converted_entry_shape(text, cfg):
    after = yaml.safe_load(ftr.fix_text(text, {"lumen/code", "lumen/chat-exp"}))
    code = _params(after, "lumen/code")
    assert code["model"] == "openai/qwen3.6:35b-a3b"
    assert code["api_base"] == "http://100.100.129.24:11434/v1"
    assert code["api_key"] == "none"
    assert code["extra_body"] == {"reasoning": {"effort": "none"}}
    assert code["timeout"] == 960  # untouched params survive
    chat = _params(after, "lumen/chat-exp")
    assert chat["model"] == "openai/qwen3:14b"
    assert chat["api_base"] == "http://100.98.74.87:11434/v1"
    assert chat["api_key"] == "none"  # already present, not duplicated


def test_untouched_entries_and_sections(text, cfg):
    after = yaml.safe_load(ftr.fix_text(text, {"lumen/code", "lumen/chat-exp"}))
    for name in ("nephthys-qwen14b", "lumen/handmerge", "lumen/misc", "deepseek-v4-flash"):
        assert _params(after, name) == _params(cfg, name)
    assert after["router_settings"] == cfg["router_settings"]
    assert after["general_settings"] == cfg["general_settings"]


def test_comments_survive(text):
    new = ftr.fix_text(text, {"lumen/code", "lumen/chat-exp"})
    assert "do not reroute silently" in new
    assert "Committed fixture for wq#4101" in new


def test_idempotent(text, cfg):
    once = ftr.fix_text(text, {"lumen/code", "lumen/chat-exp"})
    assert ftr.classify(yaml.safe_load(once))["fix"] == []
    assert ftr.fix_text(once, {"lumen/code", "lumen/chat-exp"}) == once


def test_check_mode_writes_nothing(tmp_path, text):
    work = tmp_path / "cfg.yaml"
    work.write_text(text)
    assert ftr.main(["--check", "--config", str(work)]) == 0
    assert work.read_text() == text
    assert not list(tmp_path.glob("*.bak.*"))


def test_apply_writes_with_backup_then_noop(tmp_path, text):
    work = tmp_path / "cfg.yaml"
    work.write_text(text)
    assert ftr.main(["--apply", "--config", str(work)]) == 0
    assert list(tmp_path.glob("cfg.yaml.bak.*_wq4101"))
    after = yaml.safe_load(work.read_text())
    assert _params(after, "lumen/code")["model"] == "openai/qwen3.6:35b-a3b"
    # second run: nothing left to fix, config byte-identical
    snap = work.read_text()
    assert ftr.main(["--apply", "--config", str(work)]) == 0
    assert work.read_text() == snap


def test_no_model_list_is_noop(tmp_path):
    work = tmp_path / "cfg.yaml"
    work.write_text("general_settings: {}\n")
    assert ftr.main(["--check", "--config", str(work)]) == 0


def test_outside_scope_config_untouched(tmp_path):
    # probe from OUTSIDE the fix's target population (wq#2630): an all-openai
    # config must classify to nothing and survive --apply byte-identical.
    body = ("model_list:\n"
            "  - model_name: nephthys-qwen14b\n"
            "    litellm_params:\n"
            "      model: openai/qwen3:14b\n"
            "      api_base: http://100.98.74.87:11434/v1\n"
            "      api_key: none\n"
            "      extra_body: {reasoning: {effort: none}}\n")
    work = tmp_path / "cfg.yaml"
    work.write_text(body)
    assert ftr.main(["--apply", "--config", str(work)]) == 0
    assert work.read_text() == body
    assert not list(tmp_path.glob("*.bak.*"))


def test_fixture_is_pristine():
    # the suite must never mutate the committed fixture
    assert "ollama/qwen3.6:35b-a3b" in FIXTURE.read_text()
