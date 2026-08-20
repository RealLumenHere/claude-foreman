"""wq#687 silent-downgrade detection tests — zero quota, synthetic RunState.
check_model_downgrade compares the init-frame model against the dispatched
model; only an UNEXPLAINED swap is a mismatch. Sanctioned safeguards redirects
(Fable->Opus 4.8/Opus 5), CLI-announced fallbacks, and local-rung ids are all
legitimate and must never flag.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from classifier import RunState
from fworker import check_model_downgrade, _norm_model


def _state(frames=()):
    st = RunState()
    for f in frames:
        st.observe(f)
    return st


def test_exact_match_no_flag():
    st = _state([{"type": "system", "subtype": "init", "model": "claude-sonnet-5"}])
    st.model = "claude-sonnet-5"
    mismatch, _ = check_model_downgrade("claude-sonnet-5", st.model, st)
    assert mismatch is False


def test_unexplained_downgrade_flags():
    # dispatched fable-5, ran sonnet-5, NO explaining frame -> real downgrade
    st = _state()
    st.model = "claude-sonnet-5"
    mismatch, detail = check_model_downgrade("claude-fable-5", st.model, st)
    assert mismatch is True
    assert "UNEXPLAINED" in detail and "claude-fable-5" in detail and "claude-sonnet-5" in detail


def test_sanctioned_opus48_redirect_no_flag():
    # Fable -> Opus 4.8 is the documented cyber safeguards landing zone
    st = _state()
    st.model = "claude-opus-4-8"
    mismatch, detail = check_model_downgrade("claude-fable-5", st.model, st)
    assert mismatch is False
    assert "EXPLAINED" in detail and "sanctioned_target=True" in detail


def test_cli_announced_refusal_fallback_no_flag():
    # even a swap to a non-sanctioned-listed target is explained if the CLI
    # emitted the fallback frame
    st = _state([
        {"type": "model_refusal_fallback", "original_model": "claude-fable-5",
         "fallback_model": "claude-opus-5", "api_refusal_category": "bio"},
    ])
    st.model = "claude-opus-5"
    mismatch, detail = check_model_downgrade("claude-fable-5", st.model, st)
    assert mismatch is False
    assert "refusal_fallbacks=1" in detail


def test_model_fallback_frame_no_flag():
    st = _state([
        {"type": "model_fallback", "reason": "overloaded",
         "fallback_model": "claude-sonnet-5"},
    ])
    st.model = "claude-sonnet-5"
    mismatch, _ = check_model_downgrade("claude-fable-5", st.model, st)
    assert mismatch is False


def test_local_rung_never_flags():
    # dispatched a local id; LiteLLM may report anything under it
    st = _state()
    st.model = "deepseek-chat-v4"
    mismatch, _ = check_model_downgrade("deepseek-v4-flash", st.model, st)
    assert mismatch is False


def test_dated_suffix_normalizes():
    st = _state()
    st.model = "claude-fable-5-20260601"
    mismatch, _ = check_model_downgrade("claude-fable-5", st.model, st)
    assert mismatch is False
    assert _norm_model("claude-fable-5-20260601") == "claude-fable-5"
    assert _norm_model("claude-fable-5") == "claude-fable-5"


def test_missing_observed_no_flag():
    # a launch failure before the init frame -> state.model is None, cannot judge
    st = _state()
    st.model = None
    mismatch, _ = check_model_downgrade("claude-fable-5", None, st)
    assert mismatch is False
