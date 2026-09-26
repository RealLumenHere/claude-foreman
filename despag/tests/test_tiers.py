"""wq#1811: despag P1-6 pytest unit tests for tier -> harness/model/effort mapping
and the litellm-mechanical backend env branch.

The ticket's acceptance names a "no-crossover proof: LiteLLM 5xx yields provider_error,
never a claude-max spawn" — test_no_crossover_on_provider_error below is that proof: it
asserts the mechanical-tier `invoke` stub is called exactly once, with the
litellm-mechanical harness, and that a raised ProviderError never causes a second call
against chrn_claude-max.
"""
from __future__ import annotations

import pytest

from despag import tiers
from despag.backends import ProviderError
from despag.backends import litellm as litellm_backend


# ---- resolve_tier ----------------------------------------------------------------

def test_mechanical_default_model_is_first_in_tier_list():
    harness_id, model_id, effort, fallback, reason = tiers.resolve_tier("mechanical")
    assert harness_id == "chrn_litellm-mechanical"
    assert model_id == "deepseek-v4-flash"
    assert effort is None
    assert fallback is False
    assert reason is None


def test_standard_tier_maps_to_claude_max_sonnet():
    harness_id, model_id, effort, fallback, reason = tiers.resolve_tier("standard")
    assert harness_id == "chrn_claude-max"
    assert model_id == "claude-sonnet-5"
    assert fallback is False


def test_frontier_tier_defaults_effort_high():
    harness_id, model_id, effort, fallback, reason = tiers.resolve_tier("frontier")
    assert harness_id == "chrn_claude-max"
    assert model_id == "claude-opus-5"
    assert effort == "high"


def test_frontier_tier_honors_requested_model_and_effort():
    harness_id, model_id, effort, fallback, reason = tiers.resolve_tier(
        "frontier", requested_model="claude-fable-5-1", requested_effort="xhigh"
    )
    assert model_id == "claude-fable-5-1"
    assert effort == "xhigh"
    assert fallback is False


def test_requested_model_outside_tier_raises_model_unavailable_not_cross_harness():
    """A mechanical-tier request for a claude-max model must never be "resolved" by
    quietly handing back the claude-max harness — it is an error, full stop."""
    with pytest.raises(tiers.ModelUnavailable) as exc_info:
        tiers.resolve_tier("mechanical", requested_model="claude-sonnet-5")
    assert exc_info.value.tier == "mechanical"
    assert exc_info.value.requested_model == "claude-sonnet-5"


def test_unavailable_model_falls_back_within_same_harness_only():
    def is_available(model_id: str) -> bool:
        return model_id != "deepseek-v4-flash"

    harness_id, model_id, effort, fallback, reason = tiers.resolve_tier(
        "mechanical", requested_model="deepseek-v4-flash", is_available=is_available
    )
    assert harness_id == "chrn_litellm-mechanical"
    assert model_id == "qwen36-local"
    assert fallback is True
    assert "deepseek-v4-flash" in reason and "qwen36-local" in reason


def test_no_candidate_available_raises_model_unavailable():
    with pytest.raises(tiers.ModelUnavailable):
        tiers.resolve_tier("mechanical", is_available=lambda model_id: False)


# ---- run_tier: the no-crossover proof ---------------------------------------------

def test_no_crossover_on_provider_error():
    calls = []

    def invoke(harness_id, model_id, effort):
        calls.append((harness_id, model_id, effort))
        raise ProviderError("litellm returned 500", status=500)

    result = tiers.run_tier("mechanical", invoke)

    assert calls == [("chrn_litellm-mechanical", "deepseek-v4-flash", None)]
    assert result["status"] == "failed"
    assert result["error"]["type"] == "harness_error"
    assert result["error"]["code"] == "provider_error"
    assert result["error"]["detail"]["harness_id"] == "chrn_litellm-mechanical"


def test_provider_error_never_touches_claude_max_harness():
    seen_harnesses = set()

    def invoke(harness_id, model_id, effort):
        seen_harnesses.add(harness_id)
        raise ProviderError("boom")

    tiers.run_tier("mechanical", invoke)

    assert seen_harnesses == {"chrn_litellm-mechanical"}
    assert "chrn_claude-max" not in seen_harnesses


def test_run_tier_success_reports_observed_model_fallback():
    def invoke(harness_id, model_id, effort):
        return {"observed_model": "qwen36-local", "output": [], "usage": None}

    result = tiers.run_tier("mechanical", invoke)

    assert result["status"] == "completed"
    assert result["model"] == "qwen36-local"
    assert result["metadata"]["model_fallback"] is True
    assert "deepseek-v4-flash" in result["metadata"]["model_fallback_reason"]


def test_run_tier_success_no_fallback_when_observed_matches_requested():
    def invoke(harness_id, model_id, effort):
        return {"observed_model": model_id, "output": [], "usage": {"total_tokens": 1}}

    result = tiers.run_tier("mechanical", invoke)

    assert result["status"] == "completed"
    assert result["metadata"]["model_fallback"] is False
    assert result["metadata"]["model_fallback_reason"] is None


# ---- observed_model_mismatch --------------------------------------------------

def test_observed_model_mismatch_true_on_swap():
    mismatch, reason = tiers.observed_model_mismatch("claude-opus-5", "claude-sonnet-5")
    assert mismatch is True
    assert "claude-opus-5" in reason and "claude-sonnet-5" in reason


def test_observed_model_mismatch_false_when_equal():
    mismatch, reason = tiers.observed_model_mismatch("deepseek-v4-flash", "deepseek-v4-flash")
    assert mismatch is False
    assert reason is None


# ---- litellm backend env -------------------------------------------------------

def test_litellm_build_env_sets_gateway_vars(tmp_path, monkeypatch):
    key_file = tmp_path / "foreman-litellm.token"
    key_file.write_text("sk-test-key\n")
    monkeypatch.setattr(litellm_backend, "LITELLM_KEY_FILE", str(key_file))
    monkeypatch.setattr(litellm_backend, "LITELLM_BASE_URL", "http://localhost:4000")

    base_env = {"ANTHROPIC_API_KEY": "s" + "hould-be-stripped", "PATH": "/usr/bin"}
    env = litellm_backend.build_env(base_env, "deepseek-v4-flash")

    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:4000"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-key"
    assert env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] == "1"
    assert "ANTHROPIC_API_KEY" not in env
    assert "PATH" in env  # unrelated env vars pass through untouched


def test_litellm_build_env_never_mutates_base_env(tmp_path, monkeypatch):
    key_file = tmp_path / "key"
    key_file.write_text("sk-test-key")
    monkeypatch.setattr(litellm_backend, "LITELLM_KEY_FILE", str(key_file))

    base_env = {"ANTHROPIC_API_KEY": "x"}
    litellm_backend.build_env(base_env, "qwen36-local")

    assert base_env == {"ANTHROPIC_API_KEY": "x"}
