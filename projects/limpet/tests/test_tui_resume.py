"""wq#4636 -- the TUI backend surfaces an interrupt, then resumes on next input."""
import uuid

from langgraph.checkpoint.memory import InMemorySaver

from limpet.agent.graph import build_graph
from limpet.agent.tui import GraphBackend


def _scripted(decisions):
    seq = list(decisions)

    def _plan(state):
        return seq.pop(0) if seq else {"done": True, "answer": "done"}

    return _plan


def test_tui_backend_prompts_then_resumes_on_confirm():
    calls = []
    graph = build_graph(
        _scripted([{"tool": "write_flash", "args": {}, "action_class": "destructive"}]),
        lambda tool: calls.append(tool["name"]) or {"status": "ok"},
        checkpointer=InMemorySaver(),
    )
    backend = GraphBackend(graph, str(uuid.uuid4()))

    # First operator message starts the turn; graph pauses at the sentinel gate.
    first = "".join(backend("flash the new firmware"))
    assert "CONFIRM" in first
    assert calls == []  # not dispatched yet

    # Operator's next message drives the resume.
    second = "".join(backend("CONFIRM"))
    assert calls == ["write_flash"]
    assert "done" in second.lower()
