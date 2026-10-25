"""wq#4636 -- LangGraph pilot for the Limpet Agent Processor.

Implements the graph sketched in ``scratch/research/langgraph-stateful-agents.md``
section 3 (the ``research/wq4630_langgraph_stateful_agent_graphs.md`` design this
worker followed when that scratch path was absent): the planner/executor split
the Limpet agent core already has (``limpet/planner.py:47`` ``Planner``), re-cast
as a resumable ``StateGraph`` so a turn can pause at a human/async boundary and
resume from the last checkpoint across process restarts.

Topology (research doc "Modeling the Limpet planner/executor split as a graph")::

    START -> planner
    planner --{device_detected}--> target_wizard (subgraph) --> planner
    planner --{action_class == read}------------------------> tool_call
    planner --{action_class in (caution, destructive)}-------> sentinel
    planner --{done}----------------------------------------> END
    sentinel --{approved}--> tool_call
    sentinel --{denied}----> result
    tool_call -> executor
    executor --{async tool}--> watchdog_wait --> result
    executor --{sync tool}---------------------> result
    result -> planner

The two interrupt points are the primitive LangGraph adds over today's loop
(research doc section "sentinel_approval node"):

  * ``sentinel`` -- ``interrupt()`` on a ``caution``/``destructive`` action_class,
    the human-in-the-loop gate (hal-spec.md §9.3). Resumed by the TUI handing the
    operator's CONFIRM/CANCEL back as ``Command(resume=...)``.
  * ``watchdog_wait`` -- ``interrupt()`` after dispatching a long-running/async
    tool; resumed out-of-band by ``watchdog --callback`` (wq#4629) delivering the
    finished result through :func:`resume_from_watchdog`.

The graph is model/tool agnostic: ``plan_fn`` and ``tool_dispatch_fn`` are
injected (research doc "Integration with the Anthropic Claude API" -- call the
model from the node, do not make the graph own the model layer). The defaults
wire ``langchain-anthropic`` + ``langchain-mcp-adapters``, imported lazily so the
in-memory unit tests run with pure stubs and no network.

Checkpointing targets a dedicated database ``limpet_graph`` on lumen-pgvector,
NOT ``fleet_memory``: the Python ``PostgresSaver`` takes no schema argument, so
isolation is per-database, and keeping the opaque checkpoint blobs out of
fleet_memory avoids exposing them to the mcp_ro grant (ticket rationale).
"""
from __future__ import annotations

import os
import pathlib
from typing import Annotated, Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

#: Valid action_class values on the planner's chosen tool (hal-spec.md §9.2b).
READ = "read"
CAUTION = "caution"
DESTRUCTIVE = "destructive"
GATED_CLASSES = (CAUTION, DESTRUCTIVE)


def _append(existing: Optional[list], new: Optional[list]) -> list:
    """Reducer: concatenate warm-band entries across supersteps."""
    return (existing or []) + (new or [])


class AgentState(TypedDict, total=False):
    """State threaded through one Limpet episode.

    Every field a mid-run checkpoint must restore to resume is explicit here
    (research doc "Fleet-wide migration" note: implicit call-stack state cannot
    be checkpointed). ``history`` is the warm band ``Planner._warm_band_section``
    reads back; it accumulates, so it uses the append reducer.
    """

    goal: str
    history: Annotated[list, _append]
    # Planner output for the current turn.
    plan: Optional[dict]
    action_class: Optional[str]
    pending_tool: Optional[dict]
    # Device hot-plug (routes to the TargetWizard subgraph until handled).
    device: Optional[dict]
    device_handled: bool
    # Human gate + execution outcome.
    approved: Optional[bool]
    result: Optional[dict]
    done: bool
    answer: Optional[str]


# A planner decision is a plain dict so the stub tests and the real LLM adapter
# share one contract. Recognized keys:
#   done: bool                 -- terminal; answer holds the final text
#   answer: str                -- final answer when done
#   tool: str                  -- tool name to dispatch
#   args: dict                 -- tool arguments
#   action_class: str          -- read | caution | destructive (default: destructive)
#   async_wait: bool           -- tool is long-running; pause on watchdog_wait
PlanFn = Callable[[AgentState], dict]
ToolDispatchFn = Callable[[dict], dict]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _planner_node(state: AgentState, plan_fn: PlanFn) -> dict:
    """Build the turn's decision from live state (wraps ``Planner`` upstream).

    Returns only the fields the decision sets, so the reducer-backed ``history``
    is left untouched here.
    """
    decision = plan_fn(state)
    action_class = decision.get("action_class") or DESTRUCTIVE  # fail closed (§9.4)
    return {
        "plan": decision,
        "action_class": action_class,
        "pending_tool": _tool_from_decision(decision),
        "done": bool(decision.get("done")),
        "answer": decision.get("answer"),
    }


def _tool_from_decision(decision: dict) -> Optional[dict]:
    if decision.get("done") or not decision.get("tool"):
        return None
    return {
        "name": decision["tool"],
        "args": decision.get("args") or {},
        "action_class": decision.get("action_class") or DESTRUCTIVE,
        "async_wait": bool(decision.get("async_wait")),
    }


def _sentinel_node(state: AgentState) -> dict:
    """Human-in-the-loop gate: pause for a CONFIRM/CANCEL on a gated action.

    ``interrupt()`` checkpoints state and returns control to the caller; the TUI
    resumes with the operator's answer (truthy == approved). hal-spec.md §9.4(4):
    confirmation is per-invocation, so ``approved`` is recomputed every pass.
    """
    tool = state.get("pending_tool") or {}
    answer = interrupt(
        {
            "kind": "approval",
            "tool": tool.get("name"),
            "action_class": tool.get("action_class"),
            "args": tool.get("args"),
            "prompt": "Destructive/caution action requires confirmation. "
            "Reply CONFIRM to proceed or CANCEL to abort.",
        }
    )
    return {"approved": _is_confirm(answer)}


def _is_confirm(answer: Any) -> bool:
    if isinstance(answer, bool):
        return answer
    if isinstance(answer, dict):
        answer = answer.get("approved", answer.get("text"))
    if isinstance(answer, bool):
        return answer
    return str(answer).strip().upper() == "CONFIRM"


def _tool_call_node(state: AgentState) -> dict:
    """ToolCall: materialize the dispatch request the Executor will run."""
    tool = state.get("pending_tool") or {}
    return {"plan": {**(state.get("plan") or {}), "dispatch": dict(tool)}}


def _executor_node(state: AgentState, tool_dispatch_fn: ToolDispatchFn) -> dict:
    """Executor: dispatch a synchronous tool now; hand async ones to the watchdog.

    An ``async_wait`` tool is kicked off (dispatch records the request) and the
    real result arrives later through ``watchdog_wait``; a synchronous tool is
    run inline and its result recorded here.
    """
    tool = state.get("pending_tool") or {}
    if tool.get("async_wait"):
        return {"result": {"status": "dispatched", "tool": tool.get("name"),
                           "args": tool.get("args")}}
    result = tool_dispatch_fn(tool)
    return {"result": result,
            "history": [{"tool": tool.get("name"), "result": result}]}


def _watchdog_wait_node(state: AgentState) -> dict:
    """WatchdogWait: pause until ``watchdog --callback`` delivers the result.

    The async tool is already in flight (executor dispatched it). ``interrupt()``
    checkpoints here; the watchdog callback resumes the thread with the finished
    payload via :func:`resume_from_watchdog` (wq#4629).
    """
    tool = state.get("pending_tool") or {}
    payload = interrupt(
        {
            "kind": "watchdog_wait",
            "tool": tool.get("name"),
            "prompt": "Awaiting watchdog --callback for long-running tool result.",
        }
    )
    result = payload if isinstance(payload, dict) else {"status": "resumed", "payload": payload}
    return {"result": result,
            "history": [{"tool": tool.get("name"), "result": result}]}


def _result_node(state: AgentState) -> dict:
    """Result: fold a gated-and-denied action into the warm band, then re-plan.

    A denied action has no executor result to record, so record the denial here;
    an executed action was already recorded by executor/watchdog_wait. Clearing
    ``pending_tool``/``approved`` keeps the next planner turn from re-firing the
    same gate.
    """
    updates: dict = {"pending_tool": None, "approved": None}
    if state.get("approved") is False:
        tool = state.get("pending_tool") or {}
        updates["history"] = [{"tool": (tool or {}).get("name"), "denied": True}]
    return updates


# --- TargetWizard subgraph (entered on device_detected) --------------------


def _wizard_node(state: AgentState) -> dict:
    """Single node of the TargetWizard subgraph: ask which action to run.

    Builds the numbered wizard menu for the newly attached device (the same
    vocabulary ``limpet/hal_wizard.py`` renders) and ``interrupt()``s for the
    operator's choice, then records it and marks the device handled so the
    parent planner stops routing back here.
    """
    device = state.get("device") or {}
    options = _wizard_options(device)
    choice = interrupt(
        {
            "kind": "device_wizard",
            "device": device,
            "options": options,
            "prompt": _wizard_prompt(device, options),
        }
    )
    label = _wizard_label(options, choice)
    return {
        "device_handled": True,
        "history": [{"device_wizard": device.get("name"), "chosen": label}],
    }


def _wizard_options(device: dict) -> list:
    """Verb labels a device advertises; mirrors hal_wizard.wizard_options.

    Imported lazily from the upstream ``limpet`` package when it is importable,
    with a minimal built-in fallback so the pilot and its tests do not hard-depend
    on that package being on ``sys.path``.
    """
    try:
        from limpet import hal_wizard  # type: ignore

        return hal_wizard.wizard_options(device)
    except Exception:
        caps = set(device.get("capabilities") or ())
        opts = []
        if "spi_flash_read" in caps:
            opts.append("Dump flash contents")
        opts.append("Identify this hardware in detail")
        opts.append("Something else (describe what you want to do)")
        return opts


def _wizard_prompt(device: dict, options: list) -> str:
    try:
        from limpet import hal_wizard  # type: ignore

        return hal_wizard.build_wizard_prompt(device)
    except Exception:
        name = device.get("name") or "Unknown device"
        lines = [f"A device was just connected: {name}.", "Reply with a number:"]
        lines += [f"  {i}) {label}" for i, label in enumerate(options, 1)]
        return "\n".join(lines)


def _wizard_label(options: list, choice: Any) -> str:
    """Map the operator's reply (a 1-based number or free text) to a label."""
    if isinstance(choice, dict):
        choice = choice.get("text", choice.get("choice"))
    try:
        idx = int(str(choice).strip())
        if 1 <= idx <= len(options):
            return options[idx - 1]
    except (TypeError, ValueError):
        pass
    return str(choice)


def _build_target_wizard() -> Any:
    sub = StateGraph(AgentState)
    sub.add_node("wizard", _wizard_node)
    sub.add_edge(START, "wizard")
    sub.add_edge("wizard", END)
    return sub.compile()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _route_after_planner(state: AgentState) -> str:
    # A freshly detected, unhandled device pre-empts planning (research doc
    # "TargetWizard subgraph on device_detected").
    if state.get("device") and not state.get("device_handled"):
        return "target_wizard"
    if state.get("done"):
        return END
    if not state.get("pending_tool"):
        return END
    if state.get("action_class") in GATED_CLASSES:
        return "sentinel"
    return "tool_call"


def _route_after_sentinel(state: AgentState) -> str:
    return "tool_call" if state.get("approved") else "result"


def _route_after_executor(state: AgentState) -> str:
    tool = state.get("pending_tool") or {}
    return "watchdog_wait" if tool.get("async_wait") else "result"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_graph(
    plan_fn: PlanFn,
    tool_dispatch_fn: ToolDispatchFn,
    checkpointer: Any = None,
) -> Any:
    """Compile the Limpet agent graph.

    ``plan_fn`` and ``tool_dispatch_fn`` are injected so unit tests drive the
    topology with deterministic stubs. Pass ``checkpointer=InMemorySaver()`` for
    tests or a ``PostgresSaver`` (see :func:`postgres_checkpointer`) for durable,
    resumable runs. Without a checkpointer the ``interrupt()`` nodes cannot
    resume, so one is required for any real run.
    """
    graph = StateGraph(AgentState)
    graph.add_node("planner", lambda s: _planner_node(s, plan_fn))
    graph.add_node("target_wizard", _build_target_wizard())
    graph.add_node("sentinel", _sentinel_node)
    graph.add_node("tool_call", _tool_call_node)
    graph.add_node("executor", lambda s: _executor_node(s, tool_dispatch_fn))
    graph.add_node("watchdog_wait", _watchdog_wait_node)
    graph.add_node("result", _result_node)

    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        ["target_wizard", "sentinel", "tool_call", END],
    )
    graph.add_edge("target_wizard", "planner")
    graph.add_conditional_edges("sentinel", _route_after_sentinel, ["tool_call", "result"])
    graph.add_edge("tool_call", "executor")
    graph.add_conditional_edges("executor", _route_after_executor, ["watchdog_wait", "result"])
    graph.add_edge("watchdog_wait", "result")
    graph.add_edge("result", "planner")

    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Postgres checkpointer (lumen-pgvector / limpet_graph)
# ---------------------------------------------------------------------------

_ENV_FILE = os.environ.get("LUMEN_AGENT_ENV", "/home/lumen/agent/.env")


def _load_pgvector_env(path: str = _ENV_FILE) -> None:
    """Populate ``LUMEN_PGVECTOR_*`` from the agent env file if not already set.

    Same loader shape as ``bin/check-pgvector-invariants``; never prints a value.
    """
    p = pathlib.Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k.startswith("LUMEN_PGVECTOR_") and k not in os.environ:
            os.environ[k] = v


def pg_conninfo(dbname: str = "limpet_graph") -> str:
    """Build the libpq conninfo for the checkpoint database on lumen-pgvector.

    Reads host/port/user/password from the environment (loading the agent env
    file first). ``dbname`` defaults to the dedicated ``limpet_graph`` database,
    deliberately NOT ``fleet_memory`` -- the saver owns its tables there and the
    blobs stay out of the mcp_ro grant's reach.
    """
    _load_pgvector_env()
    host = os.environ["LUMEN_PGVECTOR_HOST"]
    port = os.environ["LUMEN_PGVECTOR_PORT"]
    user = os.environ["LUMEN_PGVECTOR_USER"]
    password = os.environ["LUMEN_PGVECTOR_PASSWORD"]
    return f"host={host} port={port} dbname={dbname} user={user} password={password}"


def postgres_checkpointer(conninfo: Optional[str] = None) -> Any:
    """Return a ``PostgresSaver`` context manager for the pilot checkpoint DB.

    Usage::

        with postgres_checkpointer() as cp:
            cp.setup()                      # idempotent; creates checkpoint tables
            graph = build_graph(plan_fn, tool_dispatch_fn, checkpointer=cp)

    Imported lazily so the module imports without ``langgraph-checkpoint-postgres``
    present (the in-memory tests never touch Postgres).
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    return PostgresSaver.from_conn_string(conninfo or pg_conninfo())


# ---------------------------------------------------------------------------
# Resume helpers (TUI + watchdog callback)
# ---------------------------------------------------------------------------


def resume_from_watchdog(graph: Any, thread_id: str, result: dict) -> dict:
    """Resume a thread paused at ``watchdog_wait`` with a finished tool result.

    This is the entry point a ``watchdog --callback`` invocation (wq#4629) calls
    when a long-running tool completes: it feeds ``result`` back into the paused
    graph as the ``interrupt()`` return value and runs on to the next pause/end.
    """
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=result), config)


def resume_turn(graph: Any, thread_id: str, answer: Any) -> dict:
    """Resume any paused interrupt (sentinel approval or wizard choice) with an answer.

    The TUI calls this with the operator's reply to drive a paused turn forward
    ("TUI drives resume"): CONFIRM/CANCEL at the sentinel gate, or a menu number
    at the device wizard.
    """
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(Command(resume=answer), config)


def start_turn(graph: Any, thread_id: str, state: AgentState) -> dict:
    """Begin a new turn for ``thread_id`` from an initial state, up to the first pause/end."""
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke(state, config)


def pending_interrupt(graph: Any, thread_id: str) -> Optional[dict]:
    """Return the payload of the interrupt a thread is paused on, or ``None``.

    Lets the TUI render the right prompt (approval vs. wizard vs. watchdog wait)
    without hard-coding which node paused.
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    interrupts = getattr(snapshot, "interrupts", None) or ()
    if not interrupts:
        return None
    return getattr(interrupts[0], "value", None)


# ---------------------------------------------------------------------------
# Default (production) plan_fn / tool_dispatch_fn
# ---------------------------------------------------------------------------


def default_plan_fn(
    workspace: Optional[str] = None,
    model: str = "claude-opus-4-8",
) -> PlanFn:
    """Build a ``plan_fn`` that calls Claude via ``langchain-anthropic``.

    The system prompt is assembled by the existing ``limpet.planner.Planner`` so
    the graph composes with (rather than replaces) today's context builder
    (research doc "compose rather than replace"). Imported lazily; raises only
    when actually invoked without the optional deps, so the unit tests that pass
    their own stub ``plan_fn`` never need ``langchain-anthropic`` installed.
    """

    def _plan(state: AgentState) -> dict:
        from langchain_anthropic import ChatAnthropic  # noqa: F401
        from limpet.planner import Planner  # type: ignore

        system = Planner(workspace=workspace).build_system_prompt()
        llm = ChatAnthropic(model=model)
        reply = llm.invoke(
            [("system", system), ("user", state.get("goal", ""))]
        )
        # The real adapter would parse tool-use blocks from ``reply``; the parsing
        # contract (the dict keys in PlanFn's docstring) is what the graph depends
        # on, kept separate from the model wiring.
        return _parse_model_reply(reply)

    return _plan


def _parse_model_reply(reply: Any) -> dict:
    """Translate a model reply into a planner-decision dict.

    Minimal pass-through: a tool-use reply becomes a tool decision, otherwise the
    text is the final answer. Kept deliberately small -- richer tool-block parsing
    belongs with the real executor integration, out of the graph's scope.
    """
    tool_calls = getattr(reply, "tool_calls", None)
    if tool_calls:
        call = tool_calls[0]
        return {
            "tool": call.get("name"),
            "args": call.get("args") or {},
            "action_class": (call.get("args") or {}).get("action_class", DESTRUCTIVE),
        }
    return {"done": True, "answer": getattr(reply, "content", str(reply))}


def default_tool_dispatch_fn(session: Any) -> ToolDispatchFn:
    """Build a ``tool_dispatch_fn`` backed by ``langchain-mcp-adapters``.

    ``session`` is a connected MCP client session (the Meta-Server). Dispatch
    calls the named tool with its args and returns a serializable result dict.
    Synchronous call path only; long-running tools route through ``watchdog_wait``
    instead and never reach here.
    """

    def _dispatch(tool: dict) -> dict:
        from langchain_mcp_adapters.tools import load_mcp_tools  # noqa: F401

        resp = session.call_tool(tool["name"], tool.get("args") or {})
        return {"status": "ok", "tool": tool["name"], "response": resp}

    return _dispatch
