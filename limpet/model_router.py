"""wq#3968 -- model-capability tier: route a tool step to a specialized model.

Split from wq#3944/A5, design: `design/wq3929_competitive_analysis.md` §5 A5
(ReCopilot). ReCopilot itself is not adoptable as a route target -- wq#3995
found its only served weights are a China-hosted API (tqgpt.qianxin.com), an
unacceptable egress for decompiled target code -- so this module is the
provider-agnostic mechanism the analysis recommended adopting, not a route to
that specific model. A verified, self-hostable alternative exists
(`limpet/examples/llm4decompile.toml`: MIT+DeepSeek license, GGUF quants from
1.3B-33B, fleet-search 2026-10-15) an operator can configure a route to.

A plugin manifest declares WHAT a step needs (`[model_routes]`,
manifest.MODEL_CAPABILITIES) -- manifest.py's job. This module decides WHICH
model answers that need, given the capability->model map an operator has
configured. Limpet's agent core and its LiteLLM client are still design-only
(`design/wq3806_limpet_architecture.md` §6: "allow routing hard steps to a
fleet model via LiteLLM"; `docs/settings-spec.md` §4.1's `llm.routing.<step>`
settings store does not exist as code yet) -- same reasoning as critic.py:
keep the routing *decision* here pure and offline-testable instead of
speculatively wiring a model client that does not exist yet. The caller (the
agent core, once built) takes the returned model name and places the actual
LiteLLM call itself.
"""
from __future__ import annotations

from manifest import MODEL_CAPABILITIES, PluginManifest

GENERAL = "general"


class RoutingError(ValueError):
    """An unroutable request -- an undeclared capability, not a missing config."""


def resolve_model(capability: str, *, capability_models: dict, resident_model: str) -> str:
    """Return the model name to use for a step that needs `capability`.

    `capability_models` is an operator-configured map of capability ->
    specialized model name (the eventual `llm.capability.<capability>`
    settings-store key; a plain dict here keeps this module settings-store-
    agnostic). "general" always resolves to `resident_model` regardless of
    `capability_models` -- it names the resident model, not a route to pick a
    replacement for it. Any other capability resolves to its configured
    model if present, else falls back to `resident_model` -- a specialized
    model is always opt-in (32GB/iGPU ceiling, `design/wq3806_limpet_architecture.md:260-266`).
    """
    if capability not in MODEL_CAPABILITIES:
        raise RoutingError(f"capability {capability!r} must be one of {MODEL_CAPABILITIES}")
    if capability == GENERAL:
        return resident_model
    return capability_models.get(capability) or resident_model


def resolve_step(manifest: PluginManifest, step_name: str, *,
                  capability_models: dict, resident_model: str) -> str:
    """Return the model name for `step_name` of `manifest`, via its declared
    `model_routes` entry (manifest.py). A step with no entry is "general" --
    undeclared steps never silently get a specialized model."""
    capability = manifest.model_routes.get(step_name, GENERAL)
    return resolve_model(capability, capability_models=capability_models, resident_model=resident_model)
