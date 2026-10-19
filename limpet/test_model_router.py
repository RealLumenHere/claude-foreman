"""Unit tests for model_router.py: the wq#3968 model-capability routing
decision. Pure, no network/model client involved (same posture as
test_critic.py). (wq#3968, split from wq#3944/A5.)"""
import os

import pytest

import manifest as m
import model_router as r

RESIDENT = "qwen3.5-9b"

EXAMPLES = os.path.join(os.path.dirname(__file__), "examples")


def test_specialized_route_when_configured():
    model = r.resolve_model(
        "re-naming",
        capability_models={"re-naming": "llm4decompile-6.7b"},
        resident_model=RESIDENT,
    )
    assert model == "llm4decompile-6.7b"


def test_fallback_to_resident_when_not_configured():
    model = r.resolve_model(
        "re-naming",
        capability_models={},
        resident_model=RESIDENT,
    )
    assert model == RESIDENT


def test_general_always_resolves_to_resident_even_if_configured():
    # "general" names the resident model; a stray capability_models entry
    # for it must never override that.
    model = r.resolve_model(
        "general",
        capability_models={"general": "llm4decompile-6.7b"},
        resident_model=RESIDENT,
    )
    assert model == RESIDENT


def test_unknown_capability_fails_closed():
    with pytest.raises(r.RoutingError):
        r.resolve_model("re-naming-ish", capability_models={}, resident_model=RESIDENT)


def test_resolve_step_uses_manifest_declared_capability():
    man = m.parse_manifest(
        '[plugin]\nname="llm4decompile"\nversion="0"\nkind="tool"\n'
        '[model_routes]\nrename_function = "re-naming"\n')
    model = r.resolve_step(
        man, "rename_function",
        capability_models={"re-naming": "llm4decompile-6.7b"},
        resident_model=RESIDENT,
    )
    assert model == "llm4decompile-6.7b"


def test_resolve_step_undeclared_step_falls_back_to_resident():
    man = m.parse_manifest('[plugin]\nname="llm4decompile"\nversion="0"\nkind="tool"\n')
    # Even with a specialized model configured, a step the manifest never
    # declared a capability for stays "general" -- no silent opt-in.
    model = r.resolve_step(
        man, "some_other_step",
        capability_models={"re-naming": "llm4decompile-6.7b"},
        resident_model=RESIDENT,
    )
    assert model == RESIDENT


def test_recopilot_routing_spec_realised_on_example_manifest():
    # wq#4001 (A5): the ReCopilot task taxonomy encoded in the committed
    # self-hostable example manifest resolves end-to-end. Probes an input from
    # OUTSIDE the mechanism's own fixtures (a real on-disk plugin, not an inline
    # literal -- wq#2630 falsifiability): parse_manifest would REJECT the file if
    # any capability name were invalid, so this can fail.
    with open(os.path.join(EXAMPLES, "llm4decompile.toml"), encoding="utf-8") as fh:
        man = m.parse_manifest(fh.read())
    specialist = {"re-naming": "llm4decompile-6.7b", "re-types": "llm4decompile-6.7b"}
    # the naming/typing steps route to the opt-in specialist ...
    assert r.resolve_step(man, "recover_function_names",
                          capability_models=specialist, resident_model=RESIDENT) == "llm4decompile-6.7b"
    assert r.resolve_step(man, "infer_variable_types",
                          capability_models=specialist, resident_model=RESIDENT) == "llm4decompile-6.7b"
    assert r.resolve_step(man, "recover_structs",
                          capability_models=specialist, resident_model=RESIDENT) == "llm4decompile-6.7b"
    # ... while the documentation (reasoning) step stays on the resident model.
    assert r.resolve_step(man, "document_function",
                          capability_models=specialist, resident_model=RESIDENT) == RESIDENT
    # and with NO specialist configured, every step falls back to resident (opt-in).
    assert r.resolve_step(man, "recover_function_names",
                          capability_models={}, resident_model=RESIDENT) == RESIDENT


# --- wq#4355: requirement-profile layer + user-override escape hatch ---

POLICY = {
    "long-context": "gpt-5.2-like-fleet-model",
    "speed-critical": "grok-like-fleet-model",
    "vision-heavy": "gemini-like-fleet-model",
}


def test_profile_routes_to_configured_policy_model():
    assert r.resolve_profile("long-context", profile_models=POLICY,
                             resident_model=RESIDENT) == "gpt-5.2-like-fleet-model"


def test_profile_falls_back_to_resident_when_policy_unset():
    # code-gen has no entry in POLICY -> resident, same opt-in posture as capabilities.
    assert r.resolve_profile("code-gen", profile_models=POLICY,
                             resident_model=RESIDENT) == RESIDENT


def test_unknown_profile_fails_closed():
    with pytest.raises(r.RoutingError):
        r.resolve_profile("gpu-melting", profile_models=POLICY, resident_model=RESIDENT)


def test_user_override_wins_over_profile_policy():
    # Perplexity axis 3: a user-pinned model beats the classifier/policy outright.
    assert r.resolve_profile("long-context", profile_models=POLICY,
                             resident_model=RESIDENT,
                             override="operator-pinned-model") == "operator-pinned-model"


def test_user_override_bypasses_profile_validation():
    # An override names a model, not a need -- so an unknown profile string must
    # NOT raise when the user has pinned a model. Probes the bypass branch
    # (wq#2630: this can fail if override were validated like a profile).
    assert r.resolve_profile("not-a-real-profile", profile_models={},
                             resident_model=RESIDENT,
                             override="pinned") == "pinned"


def test_empty_override_defers_to_policy():
    # "" / None must not be treated as a pinned model -- falls through to routing.
    assert r.resolve_profile("speed-critical", profile_models=POLICY,
                             resident_model=RESIDENT, override="") == "grok-like-fleet-model"
    assert r.resolve_profile("speed-critical", profile_models=POLICY,
                             resident_model=RESIDENT, override=None) == "grok-like-fleet-model"


def test_resolve_step_override_wins_over_manifest_capability():
    # The escape hatch at the step layer: a user pin beats the manifest's declared
    # capability and its configured specialist model.
    man = m.parse_manifest(
        '[plugin]\nname="llm4decompile"\nversion="0"\nkind="tool"\n'
        '[model_routes]\nrename_function = "re-naming"\n')
    assert r.resolve_step(man, "rename_function",
                          capability_models={"re-naming": "llm4decompile-6.7b"},
                          resident_model=RESIDENT,
                          override="user-pinned") == "user-pinned"
