"""wq#1711 acceptance tests for the difficulty router decision core.

Run: python3 -m pytest ops/wq1711-difficulty-router/test_difficulty_router.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from difficulty_router import (  # noqa: E402
    DifficultyRouterHook,
    Policy,
    Rung,
    decide,
    estimate_prompt_tokens,
    has_image,
)

LOCAL = Rung(model="qwen36-local", blended_cost_per_mtok=0.0, max_prompt_tokens=24_000,
             max_output_tokens=4_096, local=True)
GLM = Rung(model="glm-5.3-flash-zai", blended_cost_per_mtok=0.15, multimodal=True,
           reasoning=True, max_output_tokens=4_096)
FLASH = Rung(model="deepseek-v4-flash", blended_cost_per_mtok=0.219)
PRO = Rung(model="deepseek-v4-pro", blended_cost_per_mtok=0.751, reasoning=True)

POLICY = Policy(alias="lumen-auto", rungs=(FLASH, LOCAL, GLM, PRO),
                escalate_to="deepseek-v4-pro", shadow=False)


def text_req(text="hi", **kw):
    return {"model": "lumen-auto", "messages": [{"role": "user", "content": text}], **kw}


def image_req():
    return {"model": "lumen-auto", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "what is on screen"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]}]}


def test_ladder_is_cost_ordered_not_declaration_ordered():
    assert [r.model for r in POLICY.ladder()] == [
        "qwen36-local", "glm-5.3-flash-zai", "deepseek-v4-flash", "deepseek-v4-pro"]


def test_easy_text_request_lands_on_the_free_local_rung():
    d = decide(text_req(), POLICY)
    assert d.model == "qwen36-local"
    assert d.reason == "cheapest_eligible"


def test_image_turn_skips_the_text_only_local_rung():
    """wq#1589/FLEET_COST_PLAN D3: qwen36-local rejects image_url content.
    The gate must be a capability check, not a preference."""
    d = decide(image_req(), POLICY)
    assert d.model == "glm-5.3-flash-zai"
    assert d.rejected["qwen36-local"] == "no_multimodal"


def test_caller_escalate_header_wins_over_every_cost_gate():
    d = decide(text_req(), POLICY, headers={"X-Lumen-Escalate": "stall_12_turns"})
    assert d.model == "deepseek-v4-pro"
    assert d.reason == "caller_escalate:stall_12_turns"


def test_escalate_can_also_arrive_in_body_metadata():
    body = text_req()
    body["metadata"] = {"lumen_escalate": "act_parse_failed"}
    assert decide(body, POLICY).model == "deepseek-v4-pro"


def test_requested_reasoning_effort_skips_non_reasoning_rungs():
    d = decide(text_req(reasoning_effort="high"), POLICY)
    assert d.model == "glm-5.3-flash-zai"
    assert d.rejected["qwen36-local"] == "no_reasoning"


def test_low_reasoning_effort_is_not_a_difficulty_signal():
    """LPP pins reasoning_effort=low on its cheapest leg (pokemon/src/config.ts:45);
    treating that as 'hard' would promote the whole main loop."""
    assert decide(text_req(reasoning_effort="low"), POLICY).model == "qwen36-local"


def test_long_prompt_exceeds_the_local_context_ceiling():
    """50k tokens clears every cloud rung's 128k window but not the local
    llama-server's 24k, so the ceiling gate is what moves this request."""
    d = decide(text_req("x" * 200_000), POLICY)
    assert d.model == "glm-5.3-flash-zai"
    assert d.rejected["qwen36-local"] == "prompt_50000_over_24000"


def test_large_max_tokens_skips_short_output_rungs():
    d = decide(text_req(max_tokens=8_000), POLICY)
    assert d.model == "deepseek-v4-flash"
    assert d.rejected["qwen36-local"] == "output_8000_over_4096"


def test_open_breaker_removes_a_saturated_rung():
    d = decide(text_req(), POLICY, open_breakers={"qwen36-local"})
    assert d.model == "glm-5.3-flash-zai"
    assert d.rejected["qwen36-local"] == "breaker_open"


def test_falls_back_to_escalate_target_when_every_rung_is_gated():
    narrow = Policy(alias="lumen-auto", rungs=(LOCAL,), escalate_to="deepseek-v4-pro", shadow=False)
    d = decide(image_req(), narrow)
    assert d.model == "deepseek-v4-pro"
    assert d.reason == "no_rung_eligible"


def test_estimators():
    assert estimate_prompt_tokens([{"role": "user", "content": "a" * 400}]) == 100
    assert has_image(image_req()["messages"]) is True
    assert has_image(text_req()["messages"]) is False


def run_hook(hook, body):
    import asyncio

    return asyncio.run(hook.async_pre_call_hook(None, None, body, "completion"))


def test_shadow_mode_records_the_decision_without_rewriting_the_model():
    shadow = Policy(alias="lumen-auto", rungs=POLICY.rungs, escalate_to="deepseek-v4-pro", shadow=True)
    hook = DifficultyRouterHook({"lumen-auto": shadow})
    out = run_hook(hook, text_req())
    assert out["model"] == "lumen-auto"
    assert out["metadata"]["lumen_router_model"] == "qwen36-local"
    assert out["metadata"]["lumen_router_shadow"] is True


def test_live_mode_rewrites_the_model():
    hook = DifficultyRouterHook({"lumen-auto": POLICY})
    out = run_hook(hook, text_req())
    assert out["model"] == "qwen36-local"


def test_pinned_callers_are_untouched():
    hook = DifficultyRouterHook({"lumen-auto": POLICY})
    body = {"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]}
    out = run_hook(hook, body)
    assert out["model"] == "deepseek-v4-pro"
    assert "metadata" not in out


def test_breaker_opens_after_two_failures_and_clears_on_success():
    hook = DifficultyRouterHook({"lumen-auto": POLICY}, breaker_fails=2, breaker_cooldown_s=600)
    hook.note_failure("qwen36-local", now=1000.0)
    assert hook.open_breakers(1000.0) == set()
    hook.note_failure("qwen36-local", now=1001.0)
    assert hook.open_breakers(1002.0) == {"qwen36-local"}
    assert hook.open_breakers(1700.0) == set()
    hook.note_success("qwen36-local")
    assert hook.open_breakers(1002.0) == set()
