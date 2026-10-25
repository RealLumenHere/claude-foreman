"""wq#4636 -- PostgresSaver integration test against lumen-pgvector/limpet_graph.

Skips cleanly when the pgvector env/credentials or the psycopg driver are not
available (CI without a DB), so the unit suite stays the always-on gate and this
is the opt-in durable-checkpoint proof. When it runs it exercises the real
resume-across-a-fresh-saver property: pause a destructive turn at the sentinel
gate, drop the saver, open a new PostgresSaver on the same database and thread,
and confirm the turn resumes from the checkpoint Postgres persisted.
"""
import uuid

import pytest

from limpet.agent.graph import build_graph, pending_interrupt, pg_conninfo, resume_turn, start_turn

psycopg = pytest.importorskip("psycopg")
try:
    from langgraph.checkpoint.postgres import PostgresSaver
except ImportError:  # pragma: no cover
    pytest.skip("langgraph-checkpoint-postgres not installed", allow_module_level=True)


def _conninfo_or_skip():
    try:
        return pg_conninfo("limpet_graph")
    except KeyError as exc:
        pytest.skip(f"pgvector env not configured: {exc}")


def _reachable(conninfo):
    try:
        with psycopg.connect(conninfo, connect_timeout=5):
            return True
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"limpet_graph unreachable: {type(exc).__name__}")


def _scripted(decisions):
    seq = list(decisions)

    def _plan(state):
        return seq.pop(0) if seq else {"done": True, "answer": "finished"}

    return _plan


def _dispatch(calls):
    def _d(tool):
        calls.append(tool["name"])
        return {"status": "ok", "tool": tool["name"]}

    return _d


def test_postgres_checkpoint_resumes_across_fresh_saver():
    conninfo = _conninfo_or_skip()
    _reachable(conninfo)
    tid = f"it-{uuid.uuid4()}"
    calls = []

    with PostgresSaver.from_conn_string(conninfo) as cp:
        cp.setup()  # idempotent; creates the checkpoint tables in limpet_graph
        g1 = build_graph(
            _scripted([{"tool": "write_flash", "args": {}, "action_class": "destructive"}]),
            _dispatch(calls),
            checkpointer=cp,
        )
        start_turn(g1, tid, {"goal": "flash new firmware"})
        assert pending_interrupt(g1, tid)["kind"] == "approval"
        assert calls == []  # paused before dispatch

    # Fresh PostgresSaver on the same DB/thread -- the checkpoint must survive.
    with PostgresSaver.from_conn_string(conninfo) as cp2:
        g2 = build_graph(_scripted([]), _dispatch(calls), checkpointer=cp2)
        assert pending_interrupt(g2, tid)["kind"] == "approval"
        out = resume_turn(g2, tid, "CONFIRM")
        assert calls == ["write_flash"]
        assert out["done"] is True


def test_postgres_setup_is_idempotent():
    conninfo = _conninfo_or_skip()
    _reachable(conninfo)
    with PostgresSaver.from_conn_string(conninfo) as cp:
        cp.setup()
        cp.setup()  # second call must not raise
