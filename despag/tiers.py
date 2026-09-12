"""wq#1811: despag P1-6 — tier -> harness/model/effort mapping.

PHASE0_WIRING_SPEC.md RECOMMENDATION step 6 + D.2. "The tier is a harness + model +
effort triple, not a new concept" (D.2): `orchestrator/router.py assign` stays the
single policy source for WHICH tier a ticket gets; this module only maps an already-
decided tier onto a despag harness id + the model ids that harness is allowed to serve,
and resolves a caller's requested model against that list.

Model ids come from `python3 orchestrator/router.py roster` (run live 2026-09-12), not
invented: the only `routed` local-pool rungs are deepseek-v4-flash and qwen36-local —
no `cerebras` rung is entitled today, so none is listed here despite being named in the
ticket text; add one only once roster actually shows it. claude-fable-5-1 (hyphenated,
per roster) covers the ticket's "claude-fable-5.1".

Budget guard (D.2): substitution across harnesses is FORBIDDEN — a mechanical-tier
task must never fall through to claude-max on a LiteLLM error, since that silently
spends window budget on what was supposed to be a free call. `resolve_tier` enforces
this structurally: it only ever returns a model from the requested tier's OWN list, or
raises `ModelUnavailable` (-> caller's 422 `model_unavailable`, tasks.md §1.3) — it has
no branch that reaches for a different tier's harness.
"""
from __future__ import annotations

import time
from typing import Callable

from despag.backends import ProviderError

TIER_HARNESS = {
    "mechanical": "chrn_litellm-mechanical",
    "standard": "chrn_claude-max",
    "frontier": "chrn_claude-max",
}

# order matters: index 0 is the tier's default when no model is requested.
TIER_MODELS = {
    "mechanical": ("deepseek-v4-flash", "qwen36-local"),
    "standard": ("claude-sonnet-5",),
    "frontier": ("claude-opus-5", "claude-fable-5-1"),
}

# metadata.lumen_effort -> CLAUDE_CODE_EFFORT_LEVEL (fworker.py:1087-1088). Only the
# frontier tier has a tier-level default; mechanical/standard leave effort unset
# (fworker only sets CLAUDE_CODE_EFFORT_LEVEL `if effort`, FW:1087).
TIER_DEFAULT_EFFORT = {
    "mechanical": None,
    "standard": None,
    "frontier": "high",  # D.2: "high|xhigh"; xhigh/max stay a Frank override, not a default
}


class ModelUnavailable(Exception):
    """Raised instead of ever substituting a different tier's harness. Caller (the
    UHP route) turns this into 422 `model_unavailable` (errors.md §3.1) or, if a
    caller-visible response object is more appropriate, a `model_fallback_reason`
    it chooses not to satisfy — either way, this exception is the ONLY structural
    path out of `resolve_tier`, and it never names a harness outside the input tier.
    """

    def __init__(self, tier: str, requested_model: str | None):
        self.tier = tier
        self.requested_model = requested_model
        super().__init__(f"model {requested_model!r} not available on tier {tier!r}")


def resolve_tier(
    tier: str,
    requested_model: str | None = None,
    requested_effort: str | None = None,
    is_available: Callable[[str], bool] = lambda model_id: True,
) -> tuple[str, str, str | None, bool, str | None]:
    """-> (harness_id, model_id, effort, model_fallback, model_fallback_reason).

    `is_available` lets a caller wire in live health (e.g. despag.app.compute_available
    or a per-model LiteLLM probe) without this function doing any I/O itself, so it
    stays a pure, unit-testable mapping.

    - No requested_model: the tier's first available model wins (no fallback).
    - requested_model in TIER_MODELS[tier] and available: honored as-is, no fallback.
    - requested_model in TIER_MODELS[tier] but NOT available: substituted for the
      tier's next available model — WITHIN the same harness only (tasks.md §1.3
      `requested_model` / `model_fallback` / `model_fallback_reason`, reported
      honestly by the caller, never silently).
    - requested_model not in TIER_MODELS[tier] at all, or no candidate in the tier is
      available: ModelUnavailable — never resolved by reaching into another tier.
    """
    if tier not in TIER_HARNESS:
        raise ModelUnavailable(tier, requested_model)
    harness_id = TIER_HARNESS[tier]
    candidates = TIER_MODELS[tier]
    effort = requested_effort or TIER_DEFAULT_EFFORT[tier]

    if requested_model is not None and requested_model not in candidates:
        raise ModelUnavailable(tier, requested_model)

    ordered = candidates if requested_model is None else (
        (requested_model,) + tuple(m for m in candidates if m != requested_model)
    )
    for model_id in ordered:
        if is_available(model_id):
            fallback = requested_model is not None and model_id != requested_model
            reason = (
                f"requested model {requested_model!r} unavailable, substituted "
                f"{model_id!r} within harness {harness_id!r}"
                if fallback else None
            )
            return harness_id, model_id, effort, fallback, reason
    raise ModelUnavailable(tier, requested_model)


def run_tier(
    tier: str,
    invoke: Callable[[str, str, str | None], dict],
    *,
    requested_model: str | None = None,
    requested_effort: str | None = None,
    is_available: Callable[[str], bool] = lambda model_id: True,
    resp_id: str = "resp_pending",
) -> dict:
    """Resolve `tier` to a harness+model+effort, then call `invoke` EXACTLY ONCE for
    that harness. This is the structural no-crossover proof: there is no second call,
    no different-harness retry, anywhere in this function — a LiteLLM 5xx (raised by
    `invoke` as `despag.backends.ProviderError`) is caught and turned into a `failed`
    Response for the harness `resolve_tier` picked, never a claude-max spawn.

    `invoke(harness_id, model_id, effort) -> {"observed_model": str, "output": [...],
    "usage": dict|None}` is the seam a real runner (PHASE0_WIRING_SPEC.md step 2) fills
    in; tests pass a stub so this stays a pure, fast unit test with no `claude -p`
    child, LiteLLM call, or event loop involved.

    Returns a UHP Response dict (errors.md §1: harness_error is HTTP 200, embedded in
    a `status: "failed"` response, never an error_response() envelope).
    """
    harness_id, model_id, effort, model_fallback, fallback_reason = resolve_tier(
        tier, requested_model, requested_effort, is_available
    )
    metadata = {
        "requested_model": requested_model or model_id,
        "model_fallback": model_fallback,
        "model_fallback_reason": fallback_reason,
    }
    try:
        result = invoke(harness_id, model_id, effort)
    except ProviderError as exc:
        return {
            "id": resp_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "failed",
            "error": {
                "type": "harness_error",
                "code": "provider_error",
                "message": str(exc),
                "param": None,
                "detail": {"harness_id": harness_id},
            },
            "model": model_id,
            "output": [],
            "metadata": metadata,
        }
    observed_model = result.get("observed_model", model_id)
    downgraded, downgrade_reason = observed_model_mismatch(model_id, observed_model)
    if downgraded:
        metadata["model_fallback"] = True
        metadata["model_fallback_reason"] = downgrade_reason
    return {
        "id": resp_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "error": None,
        "model": observed_model,
        "output": result.get("output", []),
        "usage": result.get("usage"),
        "metadata": metadata,
    }


def observed_model_mismatch(requested_model: str, observed_model: str) -> tuple[bool, str | None]:
    """fworker's observed_model/model_downgraded pattern (FW:1899-1901, FW:207-228)
    applied to one UHP response instead of a run record: compare what the system/init
    frame reports it actually ran against what was requested, and say so honestly
    (model_fallback / model_fallback_reason) instead of reporting the requested model
    as if it were what ran."""
    if not requested_model or not observed_model or requested_model == observed_model:
        return False, None
    return True, f"requested {requested_model!r}, ran {observed_model!r}"
