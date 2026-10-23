"""wq#4512 -- tests for the first-boot LLM provider wizard."""
import tomllib

import pytest

from limpet import llm_firstboot


def _inputs(*answers):
    it = iter(answers)
    return lambda _prompt: next(it)


def test_cloud_api_choice_requires_key_file():
    answers = llm_firstboot.prompt_answers(
        input_fn=_inputs("1", "/etc/limpet/anthropic.key"), print_fn=lambda *_: None
    )
    assert answers == {"provider": "cloud-api", "api_key_file": "/etc/limpet/anthropic.key"}


def test_ollama_choice_defaults_model_when_blank():
    answers = llm_firstboot.prompt_answers(
        input_fn=_inputs("2", ""), print_fn=lambda *_: None
    )
    assert answers["provider"] == "ollama-direct"
    assert answers["resident_model"] == llm_firstboot.DEFAULT_OLLAMA_RESIDENT_MODEL


def test_none_choice():
    answers = llm_firstboot.prompt_answers(input_fn=_inputs("3"), print_fn=lambda *_: None)
    assert answers == {"provider": "none"}


def test_unrecognized_choice_raises():
    with pytest.raises(llm_firstboot.WizardError):
        llm_firstboot.prompt_answers(input_fn=_inputs("9"), print_fn=lambda *_: None)


def test_cloud_api_without_key_file_rejected():
    with pytest.raises(llm_firstboot.WizardError):
        llm_firstboot.validate_answers({"provider": "cloud-api"})


def test_render_toml_round_trips_through_tomllib():
    toml_text = llm_firstboot.render_toml(
        {"provider": "cloud-api", "api_key_file": "/etc/limpet/anthropic.key"}
    )
    doc = tomllib.loads(toml_text)
    assert doc["llm"]["provider"] == "cloud-api"
    assert doc["llm"]["api_key_file"] == "/etc/limpet/anthropic.key"


def test_write_llm_toml_creates_parent_dirs_and_file(tmp_path):
    target = tmp_path / "etc" / "limpet" / "llm.toml"
    written = llm_firstboot.write_llm_toml({"provider": "none"}, path=target)
    assert written == target
    doc = tomllib.loads(target.read_text())
    assert doc["llm"]["provider"] == "none"


def test_main_skips_when_file_exists_and_not_forced(tmp_path, capsys):
    target = tmp_path / "llm.toml"
    target.write_text("[llm]\nprovider = \"none\"\n")
    rc = llm_firstboot.main(["--path", str(target)])
    assert rc == 0
    assert "skipping" in capsys.readouterr().out
