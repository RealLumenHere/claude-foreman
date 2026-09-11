"""Difficulty-based rung selection for the LiteLLM gateway (wq#1711).

The decision core is a pure function so it can be tested without LiteLLM, a
network, or a running proxy. `DifficultyRouterHook` is the thin adapter that
LiteLLM 1.83.10 loads via `general_settings.callbacks`.

Contract:
  * Only requests whose `model` is a declared auto-alias are touched. Every
    pinned caller keeps its rung, so installing this changes nothing until a
    caller opts in by asking for the alias.
  * `shadow: true` computes and records the decision but does not rewrite the
    request. That is the measurement mode; see research/DIFFICULTY_ROUTING.md §6.
  * The escalate signal is the caller's, not ours: `x-lumen-escalate: <reason>`
    or `metadata.lumen_escalate`. Present means "start at the top rung".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

ESCALATE_HEADER = "x-lumen-escalate"
ESCALATE_META_KEY = "lumen_escalate"


@dataclass(frozen=True)
class Rung:
    """One deployment the router may choose, with the limits that disqualify it."""

    model: str
    blended_cost_per_mtok: float
    multimodal: bool = False
    reasoning: bool = False
    max_prompt_tokens: int = 128_000
    max_output_tokens: int = 32_000
    local: bool = False


@dataclass(frozen=True)
class Policy:
    """Rungs for one auto-alias, plus the escalation target."""

    alias: str
    rungs: tuple[Rung, ...]
    escalate_to: str
    shadow: bool = True

    def ladder(self) -> list[Rung]:
        return sorted(self.rungs, key=lambda r: r.blended_cost_per_mtok)


@dataclass
class Decision:
    model: str
    reason: str
    shadow: bool
    considered: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "lumen_router_model": self.model,
            "lumen_router_reason": self.reason,
            "lumen_router_shadow": self.shadow,
            "lumen_router_rejected": self.rejected,
        }


def estimate_prompt_tokens(messages: Iterable[Any]) -> int:
    """Chars/4 over every text part. Deliberately provider-agnostic and cheap:
    this runs on the request path and only ever gates a coarse ceiling."""
    chars = 0
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chars += len(part["text"])
    return chars // 4


def has_image(messages: Iterable[Any]) -> bool:
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    return True
    return False


def wants_reasoning(body: dict[str, Any]) -> bool:
    effort = body.get("reasoning_effort")
    if isinstance(effort, str) and effort.lower() in ("medium", "high"):
        return True
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") not in (None, "disabled"):
        return True
    return False


def escalate_reason(body: dict[str, Any], headers: dict[str, str] | None) -> str | None:
    for key, value in (headers or {}).items():
        if key.lower() == ESCALATE_HEADER and value:
            return str(value)
    meta = body.get("metadata")
    if isinstance(meta, dict) and meta.get(ESCALATE_META_KEY):
        return str(meta[ESCALATE_META_KEY])
    return None


def decide(
    body: dict[str, Any],
    policy: Policy,
    headers: dict[str, str] | None = None,
    open_breakers: set[str] | None = None,
) -> Decision:
    """Pick the cheapest rung that can actually serve this request.

    Order matters: the escalate signal wins outright, then hard capability
    gates, then cost. A gate never promotes past `escalate_to`."""
    open_breakers = open_breakers or set()
    ladder = policy.ladder()
    decision = Decision(model=policy.escalate_to, reason="no_rung_eligible", shadow=policy.shadow)
    decision.considered = [r.model for r in ladder]

    reason = escalate_reason(body, headers)
    if reason:
        decision.model = policy.escalate_to
        decision.reason = f"caller_escalate:{reason}"
        return decision

    messages = body.get("messages") or []
    image = has_image(messages)
    prompt_tokens = estimate_prompt_tokens(messages)
    max_output = int(body.get("max_tokens") or 0)
    reasoning = wants_reasoning(body)

    for rung in ladder:
        if image and not rung.multimodal:
            decision.rejected[rung.model] = "no_multimodal"
            continue
        if reasoning and not rung.reasoning:
            decision.rejected[rung.model] = "no_reasoning"
            continue
        if prompt_tokens > rung.max_prompt_tokens:
            decision.rejected[rung.model] = f"prompt_{prompt_tokens}_over_{rung.max_prompt_tokens}"
            continue
        if max_output > rung.max_output_tokens:
            decision.rejected[rung.model] = f"output_{max_output}_over_{rung.max_output_tokens}"
            continue
        if rung.model in open_breakers:
            decision.rejected[rung.model] = "breaker_open"
            continue
        decision.model = rung.model
        decision.reason = "cheapest_eligible"
        return decision

    return decision


class DifficultyRouterHook:
    """LiteLLM adapter. Subclass `litellm.integrations.custom_logger.CustomLogger`
    at install time; kept base-free here so the tests need no LiteLLM import."""

    def __init__(self, policies: dict[str, Policy], breaker_fails: int = 2, breaker_cooldown_s: int = 600):
        self.policies = policies
        self.breaker_fails = breaker_fails
        self.breaker_cooldown_s = breaker_cooldown_s
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def open_breakers(self, now: float) -> set[str]:
        return {m for m, until in self._open_until.items() if until > now}

    def note_failure(self, model: str, now: float) -> None:
        """Mirrors the LPP brain breaker (pokemon/src/brain.ts:119) so a
        saturated local rung stops being offered instead of being retried."""
        count = self._fails.get(model, 0) + 1
        if count < self.breaker_fails:
            self._fails[model] = count
            return
        self._fails[model] = 0
        self._open_until[model] = now + self.breaker_cooldown_s

    def note_success(self, model: str) -> None:
        self._fails.pop(model, None)
        self._open_until.pop(model, None)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        import time

        policy = self.policies.get(data.get("model", ""))
        if policy is None:
            return data
        headers = (data.get("proxy_server_request") or {}).get("headers") or {}
        decision = decide(data, policy, headers=headers, open_breakers=self.open_breakers(time.time()))
        metadata = dict(data.get("metadata") or {})
        metadata.update(decision.as_metadata())
        data["metadata"] = metadata
        if not policy.shadow:
            data["model"] = decision.model
        return data
