"""wq#4511 -- multi-agent RE topology: supervisor / static / dynamic.

`design/wq3806_limpet_architecture.md` §6 describes a SINGLE agent loop (identify
-> choose toolset -> orchestrate -> verify -> report). This module adds the
three-agent topology the Phase 4 ticket names on top of that loop:

  Supervisor      role `agent-deep`  -- orchestrates; owns the plan, delegates
                                        tool work to the two workers, adjudicates
                                        escalations. Runs no bench tool itself.
  Static Analyst  role `agent-code`  -- disassembly / decompilation / firmware
                                        static recon. Sees only static tools.
  Dynamic Agent   role `agent-fast`  -- runtime: emulation/debug, fuzzing, and
                                        on-device flash/bus work. Sees only
                                        dynamic tools.

Same pure-decision posture as `model_router.py` and `critic.py`: Limpet's agent
core and its LiteLLM/MCP clients are design-only, so this module decides the
WIRING -- which LLM role each agent runs on (`model_router.AGENT_ROLES`), which
tool categories each agent is allowed to see (`manifest.CATEGORIES`), and what the
confidence loop does next -- and the caller (the agent core, once built) resolves
roles to models, mounts the filtered toolset, and places the actual calls. Nothing
here imports a model or MCP client.

The role split is the orchestrator-workers pattern (fleet-search web 2026-10-23:
"Agentic AI Design Patterns" / "OpenClaw Orchestration Patterns" -- a central
model plans and delegates to specialized workers at runtime); the confidence loop
is the same reflect-retry-then-escalate shape, made a pure, offline-testable
decision here.
"""
from __future__ import annotations

import dataclasses

from manifest import CATEGORIES
from model_router import AGENT_CODE, AGENT_DEEP, AGENT_FAST, AGENT_ROLES

# The three RE agents.
SUPERVISOR = "supervisor"
STATIC = "static"
DYNAMIC = "dynamic"

# A low-confidence result with no alternative approach left escalates UP: a worker
# hands the problem to the supervisor; the supervisor, having no agent above it,
# hands it to the operator (design/wq3806 §6 autonomy boundary -- the human owns
# the decisions the loop cannot close).
OPERATOR = "operator"


class TopologyError(ValueError):
    """An undefined agent, role, or tool category -- a caller bug, not config."""


@dataclasses.dataclass(frozen=True)
class AgentSpec:
    """One agent in the topology: its LLM role, the tool categories it is allowed
    to invoke (its filtered MCP view), the agents it may delegate to, and a
    one-line purpose. Frozen -- the topology is a fixed contract, not state."""

    name: str
    role: str
    allowed_categories: frozenset
    delegates_to: frozenset
    purpose: str

    def __post_init__(self):
        if self.role not in AGENT_ROLES:
            raise TopologyError(
                f"agent {self.name!r} role {self.role!r} must be one of {AGENT_ROLES}")
        unknown = self.allowed_categories - set(CATEGORIES)
        if unknown:
            raise TopologyError(
                f"agent {self.name!r} allows unknown tool categor(ies) "
                f"{sorted(unknown)}; must be a subset of {CATEGORIES}")


# Tool-category filter per agent, over the closed manifest.CATEGORIES taxonomy.
# Static and Dynamic partition the analysis tools so a worker can never reach the
# other's toolset: a disassembler/decompiler is a static instrument; a debugger/
# fuzzer/flash-programmer/SDR drives a running target. "network" is dynamic (it
# observes a live device's traffic). The Supervisor runs NO bench tool -- it
# orchestrates the two workers -- so its allowed set is empty and it reaches tools
# only through delegation. "other" (manifest's escape-hatch category) is granted to
# both workers so an uncategorized plugin is not silently stranded.
_STATIC_CATEGORIES = frozenset({"disassembler", "decompiler", "firmware", "data", "other"})
_DYNAMIC_CATEGORIES = frozenset(
    {"debugger", "fuzzer", "flash-programmer", "sdr", "network", "other"})

AGENTS = {
    SUPERVISOR: AgentSpec(
        name=SUPERVISOR,
        role=AGENT_DEEP,
        allowed_categories=frozenset(),
        delegates_to=frozenset({STATIC, DYNAMIC}),
        purpose="orchestrate the plan and delegate tool work to the static and "
                "dynamic agents; adjudicate escalations",
    ),
    STATIC: AgentSpec(
        name=STATIC,
        role=AGENT_CODE,
        allowed_categories=_STATIC_CATEGORIES,
        delegates_to=frozenset(),
        purpose="static analysis: disassembly, decompilation, firmware recon",
    ),
    DYNAMIC: AgentSpec(
        name=DYNAMIC,
        role=AGENT_FAST,
        allowed_categories=_DYNAMIC_CATEGORIES,
        delegates_to=frozenset(),
        purpose="dynamic analysis: emulation/debug, fuzzing, on-device bus/flash work",
    ),
}


def spec(agent: str) -> AgentSpec:
    """The AgentSpec for `agent`, or raise TopologyError for an unknown name."""
    try:
        return AGENTS[agent]
    except KeyError:
        raise TopologyError(
            f"unknown agent {agent!r}; must be one of {tuple(AGENTS)}") from None


def role_for(agent: str) -> str:
    """The LLM role (a `model_router.AGENT_ROLES` value) `agent` runs on. The
    caller maps role->model via `model_router.resolve_agent_role`."""
    return spec(agent).role


def tools_for(agent: str, catalog) -> list:
    """Filter a tool `catalog` to the entries `agent` is allowed to invoke -- the
    agent's filtered MCP view.

    `catalog` is any iterable of tool records (dicts), each carrying a `category`
    (a `manifest.CATEGORIES` value, the taxonomy `limpet-tools` already indexes on).
    A record whose category is not in the agent's allowed set is dropped; a record
    with a missing or unknown category is dropped too -- fail-closed, so an
    unlabeled tool is never silently exposed to a worker, and the Supervisor (empty
    allowed set) is handed nothing to run directly.
    """
    allowed = spec(agent).allowed_categories
    return [t for t in catalog if t.get("category") in allowed]


def can_delegate(agent: str, to: str) -> bool:
    """Whether `agent` may hand work to sub-agent `to`. Only the Supervisor
    delegates (to either worker); workers do not delegate laterally -- a worker
    that cannot make progress escalates (see next_action), it does not re-dispatch."""
    if to not in AGENTS:
        raise TopologyError(f"unknown delegate target {to!r}; must be one of {tuple(AGENTS)}")
    return to in spec(agent).delegates_to


# ---- confidence-based iterative loop -------------------------------------
# Each produced result carries a confidence in [0, 1]. A result at or above the
# threshold is accepted. Below it, the agent first retries with an alternative
# approach it has not tried; once every alternative is spent, it escalates up the
# topology (worker -> supervisor -> operator). This is the reflect/retry/escalate
# pattern as a pure decision -- the caller supplies the score and the approach
# inventory and acts on the returned LoopDecision.
CONFIDENCE_THRESHOLD = 0.7

ACCEPT = "accept"
RETRY_ALTERNATIVE = "retry-alternative"
ESCALATE = "escalate"


@dataclasses.dataclass(frozen=True)
class LoopDecision:
    """What the loop should do with a scored result.

    action        one of ACCEPT / RETRY_ALTERNATIVE / ESCALATE.
    next_approach the alternative to try next (set iff RETRY_ALTERNATIVE).
    escalate_to   who the problem is handed to (set iff ESCALATE): SUPERVISOR for a
                  worker, OPERATOR for the supervisor itself.
    """

    action: str
    next_approach: str | None = None
    escalate_to: str | None = None


def next_action(agent: str, confidence: float, *, approaches_tried=(),
                approaches_available=(),
                threshold: float = CONFIDENCE_THRESHOLD) -> LoopDecision:
    """Decide what to do with a result scored `confidence` for `agent`.

    `approaches_available` is the full inventory of approaches for the step;
    `approaches_tried` are the ones already attempted (order-preserving, dedup'd
    against availability). At or above `threshold` -> ACCEPT. Below it, if any
    listed approach is still untried -> RETRY_ALTERNATIVE with the first such
    approach. Otherwise -> ESCALATE: a worker to the Supervisor, the Supervisor
    (nothing above it) to the OPERATOR.

    Falsifiable by construction: an accept decision requires confidence to clear
    the bar, a retry requires an untried alternative to exist, and the escalate
    target flips with whether the agent is the supervisor.
    """
    spec(agent)  # validate the agent name up front
    if not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
        raise TopologyError(f"confidence must be a number in [0, 1], got {confidence!r}")

    if confidence >= threshold:
        return LoopDecision(ACCEPT)

    tried = set(approaches_tried)
    remaining = [a for a in approaches_available if a not in tried]
    if remaining:
        return LoopDecision(RETRY_ALTERNATIVE, next_approach=remaining[0])

    escalate_to = OPERATOR if agent == SUPERVISOR else SUPERVISOR
    return LoopDecision(ESCALATE, escalate_to=escalate_to)
