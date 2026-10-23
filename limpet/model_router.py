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

# wq#4355 -- requirement profiles: a task-classifier->model-policy layer above
# raw capabilities. Perplexity Computer (design/wq4355_perplexity_computer_dissection.md)
# does not ask operators to name models per step; it classifies each subtask by
# what it NEEDS and routes to the model specialized for that need (Opus for
# reasoning, GPT-5.2 for long-context, Grok for speed, Gemini/Nano-Banana for
# research/vision). These four are the RE-subtask requirement profiles the ticket
# names. Like MODEL_CAPABILITIES this is a closed set: an unknown profile is a
# bug in the caller, not a missing config, so it fails closed (wq#2630 fail-closed
# posture). The profile->model map an operator configures is the "policy"; this
# module stays the pure decision, same posture as resolve_model above.
REQUIREMENT_PROFILES = ("vision-heavy", "long-context", "speed-critical", "code-gen")


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


def resolve_profile(profile: str, *, profile_models: dict, resident_model: str,
                    override: str | None = None) -> str:
    """Return the model name for a subtask whose requirement `profile` says what
    it NEEDS (one of REQUIREMENT_PROFILES), not which model to use.

    This is the wq#4355 task-classifier->model-policy layer. `profile_models` is
    the operator-configured policy (profile -> specialized model); a profile with
    no entry falls back to `resident_model`, same opt-in posture as resolve_model.

    `override` is the user-override escape hatch (Perplexity Computer axis 3,
    `design/wq4355_perplexity_computer_dissection.md` §4): a non-empty model name
    the user pinned to this subtask. It wins over the classifier and the policy
    outright -- a manual assignment deliberately bypasses the routing decision,
    so it is NOT validated against REQUIREMENT_PROFILES (the user is naming a
    model, not a need). An empty/None override defers to the policy."""
    if override:
        return override
    if profile not in REQUIREMENT_PROFILES:
        raise RoutingError(f"profile {profile!r} must be one of {REQUIREMENT_PROFILES}")
    return profile_models.get(profile) or resident_model


# wq#4510 -- agent-role split. The fleet's LiteLLM alias layer already names two
# roles (`ops/wq3849-m80s-llm-deployment/litellm-aliases.yaml:10-14`): `agent-fast`
# (MoE, interactive chat and simple tool dispatch) and `agent-deep` (reasoning
# model, multi-step plans and firmware/decompilation work). `docs/settings-spec.md`
# §4.1 names the corresponding settings keys `llm.role.agent_fast` / `llm.role.agent_deep`.
# Same pure-decision posture as resolve_model: the classifier below names the ROLE a
# request needs; the caller (the agent core) maps role->model via its configured
# role_models and places the LiteLLM call itself.
AGENT_FAST = "agent-fast"
AGENT_DEEP = "agent-deep"
AGENT_ROLES = (AGENT_FAST, AGENT_DEEP)

# Signals that a request (or its prior context) is RE work the reasoning model
# should take: firmware analysis, decompilation, or a multi-step RE workflow.
# A closed, lowercase keyword set -- matched as substrings so "decompile",
# "decompiler", "decompilation" all hit one entry. Kept here (not in the caller)
# so the heuristic is pure and offline-testable, same reason as resolve_model.
_DEEP_SIGNALS = (
    "firmware",
    "decompil",
    "disassembl",
    "reverse engineer",
    "reverse-engineer",
    "reverse engineering",
    "binary analysis",
    "ghidra",
    "multi-step",
    "multistep",
)


def classify_agent_role(request: str, *, prior_context: str = "") -> str:
    """Return the agent role a chat turn needs: AGENT_DEEP for firmware analysis,
    decompilation, or multi-step RE workflows, else AGENT_FAST.

    The wq#4510 decision heuristic. `request` is the current user message;
    `prior_context` is earlier conversation text that can also pull a turn deep
    (the ticket's "or prior context involves ..." clause) -- a follow-up like
    "and the next function?" inherits the reasoning model from the firmware
    thread it belongs to. Matching is case-insensitive substring over a closed
    signal set; the default is the fast MoE model, so routing deep is always a
    positive match, never a guess. Returns a role name, not a model -- the caller
    resolves it through resolve_agent_role."""
    haystack = f"{request}\n{prior_context}".lower()
    if any(signal in haystack for signal in _DEEP_SIGNALS):
        return AGENT_DEEP
    return AGENT_FAST


def resolve_agent_role(role: str, *, role_models: dict, resident_model: str,
                       override: str | None = None) -> str:
    """Return the model name for an agent `role` (one of AGENT_ROLES).

    `role_models` is the operator-configured policy (role -> model name; the
    `llm.role.agent_fast` / `llm.role.agent_deep` settings keys). A role with no
    entry falls back to `resident_model`, same opt-in posture as resolve_model --
    a box with no deep model configured still answers, on the resident model.
    `override` is the wq#4355 user-pin escape hatch: a non-empty model name wins
    over the classifier and policy outright, so it is NOT validated against
    AGENT_ROLES (the user is naming a model, not a role). An empty/None override
    defers to the policy."""
    if override:
        return override
    if role not in AGENT_ROLES:
        raise RoutingError(f"role {role!r} must be one of {AGENT_ROLES}")
    return role_models.get(role) or resident_model


def route_agent_turn(request: str, *, role_models: dict, resident_model: str,
                     prior_context: str = "", override: str | None = None) -> str:
    """Classify a chat turn (classify_agent_role) and resolve it to a model name
    (resolve_agent_role) in one call -- the agent core's entry point for the
    wq#4510 reasoning-vs-fast split. `override` short-circuits classification,
    same escape hatch as resolve_agent_role."""
    if override:
        return override
    role = classify_agent_role(request, prior_context=prior_context)
    return resolve_agent_role(role, role_models=role_models, resident_model=resident_model)


def resolve_step(manifest: PluginManifest, step_name: str, *,
                  capability_models: dict, resident_model: str,
                  override: str | None = None) -> str:
    """Return the model name for `step_name` of `manifest`, via its declared
    `model_routes` entry (manifest.py). A step with no entry is "general" --
    undeclared steps never silently get a specialized model. `override` is the
    user-override escape hatch (wq#4355): a model the user pinned to this step
    wins over the manifest's declared capability and its configured model."""
    if override:
        return override
    capability = manifest.model_routes.get(step_name, GENERAL)
    return resolve_model(capability, capability_models=capability_models, resident_model=resident_model)
