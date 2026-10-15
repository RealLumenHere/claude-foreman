"""Unit tests for model_router.py: the wq#3968 model-capability routing
decision. Pure, no network/model client involved (same posture as
test_critic.py). (wq#3968, split from wq#3944/A5.)"""
import pytest

import manifest as m
import model_router as r

RESIDENT = "qwen3.5-9b"


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
