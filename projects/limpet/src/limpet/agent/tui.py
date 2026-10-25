"""wq#4636 -- TUI driver that resumes the agent graph from operator input.

``limpet/agent_tui.py``'s ``AgentSession`` takes a ``backend: text -> Iterable[str]``
that yields one turn's reply chunks. :class:`GraphBackend` is that callable backed
by the LangGraph graph: the first message of a turn starts it; while the graph is
paused on an ``interrupt()`` (sentinel approval or device wizard) the next operator
message is fed back as the resume value. This is the "TUI drives resume" path --
the operator types CONFIRM / CANCEL / a menu number and the graph continues from
its checkpoint.

Watchdog-driven resumes (long-running tools) do NOT come through here: the
watchdog callback resumes out-of-band via ``graph.resume_from_watchdog``; this
driver only surfaces that the turn is parked.
"""
from __future__ import annotations

from typing import Any, Iterable, Iterator

from .graph import pending_interrupt, resume_turn, start_turn


class GraphBackend:
    """Adapt a compiled agent graph to ``AgentSession``'s backend contract."""

    def __init__(self, graph: Any, thread_id: str):
        self.graph = graph
        self.thread_id = thread_id

    def __call__(self, text: str) -> Iterable[str]:
        return self._drive(text)

    def _drive(self, text: str) -> Iterator[str]:
        pending = pending_interrupt(self.graph, self.thread_id)
        if pending is not None:
            # Mid-turn: this message answers the outstanding interrupt.
            if pending.get("kind") == "watchdog_wait":
                yield "Still waiting on watchdog --callback for the running tool."
                return
            out = resume_turn(self.graph, self.thread_id, text)
        else:
            out = start_turn(self.graph, self.thread_id, {"goal": text})
        yield from self._render(out)

    def _render(self, out: dict) -> Iterator[str]:
        pending = pending_interrupt(self.graph, self.thread_id)
        if pending is not None:
            yield pending.get("prompt", "Awaiting your input.")
            return
        if out.get("answer"):
            yield str(out["answer"])
        else:
            yield "Turn complete."
