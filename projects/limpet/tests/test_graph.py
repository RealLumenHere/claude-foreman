"""wq#4636 -- InMemorySaver unit tests for the Limpet agent graph.

These drive the topology with deterministic stub ``plan_fn``/``tool_dispatch_fn``
so they need no network, no Anthropic key, and no Postgres. They exercise each
route: a read action that runs straight through, the sentinel approval gate
(CONFIRM and CANCEL), the watchdog-wait pause resumed by a callback, and the
TargetWizard subgraph entered on device_detected.
"""
import uuid

from langgraph.checkpoint.memory import InMemorySaver

from limpet.agent.graph import (
    build_graph,
    pending_interrupt,
    resume_from_watchdog,
    resume_turn,
    start_turn,
)


def _tid():
    return str(uuid.uuid4())


def _scripted(decisions):
    """A plan_fn yielding the given decisions in order, then a terminal answer."""
    seq = list(decisions)

    def _plan(state):
        if seq:
            return seq.pop(0)
        return {"done": True, "answer": "finished"}

    return _plan


def _echo_dispatch(calls):
    def _dispatch(tool):
        calls.append(tool)
        return {"status": "ok", "tool": tool["name"], "echo": tool.get("args")}

    return _dispatch


def test_read_action_runs_without_interrupt():
    calls = []
    graph = build_graph(
        _scripted([{"tool": "dump_flash", "args": {"dev": "spi0"}, "action_class": "read"}]),
        _echo_dispatch(calls),
        checkpointer=InMemorySaver(),
    )
    out = start_turn(graph, _tid(), {"goal": "read the flash"})

    assert out["done"] is True
    assert calls == [{"name": "dump_flash", "args": {"dev": "spi0"},
                      "action_class": "read", "async_wait": False}]
    assert out["history"][0]["tool"] == "dump_flash"
    assert out["history"][0]["result"]["status"] == "ok"


def test_destructive_action_pauses_at_sentinel_then_confirm_runs_it():
    calls = []
    graph = build_graph(
        _scripted([{"tool": "write_flash", "args": {}, "action_class": "destructive"}]),
        _echo_dispatch(calls),
        checkpointer=InMemorySaver(),
    )
    tid = _tid()
    paused = start_turn(graph, tid, {"goal": "flash new firmware"})

    # Paused at the human gate -- tool not dispatched yet.
    assert calls == []
    gate = pending_interrupt(graph, tid)
    assert gate["kind"] == "approval"
    assert gate["action_class"] == "destructive"
    assert "__interrupt__" in paused

    out = resume_turn(graph, tid, "CONFIRM")
    assert [c["name"] for c in calls] == ["write_flash"]
    assert out["done"] is True


def test_destructive_action_cancel_skips_dispatch_and_records_denial():
    calls = []
    graph = build_graph(
        _scripted([{"tool": "write_flash", "args": {}, "action_class": "destructive"}]),
        _echo_dispatch(calls),
        checkpointer=InMemorySaver(),
    )
    tid = _tid()
    start_turn(graph, tid, {"goal": "flash new firmware"})
    out = resume_turn(graph, tid, "CANCEL")

    assert calls == []  # hard gate held
    denials = [h for h in out["history"] if h.get("denied")]
    assert denials and denials[0]["tool"] == "write_flash"
    assert out["done"] is True


def test_caution_action_is_also_gated():
    calls = []
    graph = build_graph(
        _scripted([{"tool": "adb_shell", "args": {}, "action_class": "caution"}]),
        _echo_dispatch(calls),
        checkpointer=InMemorySaver(),
    )
    tid = _tid()
    start_turn(graph, tid, {"goal": "poke the shell"})
    assert pending_interrupt(graph, tid)["kind"] == "approval"
    resume_turn(graph, tid, "CONFIRM")
    assert [c["name"] for c in calls] == ["adb_shell"]


def test_missing_action_class_defaults_to_destructive_gate():
    # hal-spec.md §9.4(2): a missing action_class is treated as destructive.
    calls = []
    graph = build_graph(
        _scripted([{"tool": "mystery_tool", "args": {}}]),
        _echo_dispatch(calls),
        checkpointer=InMemorySaver(),
    )
    tid = _tid()
    start_turn(graph, tid, {"goal": "run the unclassified tool"})
    assert pending_interrupt(graph, tid)["kind"] == "approval"
    assert calls == []


def test_async_tool_pauses_at_watchdog_then_callback_resumes():
    calls = []
    graph = build_graph(
        _scripted([{"tool": "long_emulate", "args": {}, "action_class": "read",
                    "async_wait": True}]),
        _echo_dispatch(calls),
        checkpointer=InMemorySaver(),
    )
    tid = _tid()
    paused = start_turn(graph, tid, {"goal": "emulate for a while"})

    # Synchronous dispatch path not taken; parked waiting for the watchdog.
    assert calls == []
    assert pending_interrupt(graph, tid)["kind"] == "watchdog_wait"
    assert "__interrupt__" in paused

    out = resume_from_watchdog(graph, tid, {"status": "done", "artifact": "trace.bin"})
    recorded = [h for h in out["history"] if h.get("tool") == "long_emulate"]
    assert recorded and recorded[0]["result"]["artifact"] == "trace.bin"
    assert out["done"] is True


def test_device_detected_enters_target_wizard_subgraph():
    device = {"name": "FT2232H", "usb_id": "0403:6010", "hw_type": "jtag",
              "capabilities": ["jtag", "swd"]}
    graph = build_graph(
        _scripted([{"done": True, "answer": "ready"}]),
        _echo_dispatch([]),
        checkpointer=InMemorySaver(),
    )
    tid = _tid()
    paused = start_turn(graph, tid, {"goal": "", "device": device})

    wiz = pending_interrupt(graph, tid)
    assert wiz["kind"] == "device_wizard"
    assert wiz["device"]["name"] == "FT2232H"
    assert wiz["options"]  # a non-empty numbered menu
    assert "__interrupt__" in paused

    out = resume_turn(graph, tid, "1")
    assert out["device_handled"] is True
    chosen = [h for h in out["history"] if "device_wizard" in h]
    assert chosen and chosen[0]["device_wizard"] == "FT2232H"


def test_checkpoint_persists_across_graph_rebuild():
    # Same saver instance = same durable store; a rebuilt graph resumes the
    # thread from its last checkpoint (the resume-across-restart property).
    saver = InMemorySaver()
    calls = []
    g1 = build_graph(
        _scripted([{"tool": "write_flash", "args": {}, "action_class": "destructive"}]),
        _echo_dispatch(calls),
        checkpointer=saver,
    )
    tid = _tid()
    start_turn(g1, tid, {"goal": "flash"})

    # Rebuild (simulating a process restart) and resume on the same store.
    g2 = build_graph(_scripted([]), _echo_dispatch(calls), checkpointer=saver)
    assert pending_interrupt(g2, tid)["kind"] == "approval"
    out = resume_turn(g2, tid, "CONFIRM")
    assert [c["name"] for c in calls] == ["write_flash"]
    assert out["done"] is True
