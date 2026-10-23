"""Unit tests for topology.py: the wq#4511 three-agent RE topology. Pure, no
network/model/MCP client involved (same posture as test_model_router.py /
test_critic.py)."""
import pytest

import manifest as m
import model_router as r
import topology as t


# ---- agent specs ---------------------------------------------------------
def test_three_agents_on_the_named_roles():
    assert set(t.AGENTS) == {t.SUPERVISOR, t.STATIC, t.DYNAMIC}
    assert t.role_for(t.SUPERVISOR) == r.AGENT_DEEP
    assert t.role_for(t.STATIC) == r.AGENT_CODE
    assert t.role_for(t.DYNAMIC) == r.AGENT_FAST


def test_every_role_resolves_through_the_router():
    # the wiring is real end to end: each agent's role is a model_router role,
    # so the agent core can resolve it to a model.
    for agent in t.AGENTS:
        role = t.role_for(agent)
        assert role in r.AGENT_ROLES
        assert r.resolve_agent_role(role, role_models={}, resident_model="res") == "res"


def test_agent_code_is_routable_and_configurable():
    assert r.AGENT_CODE in r.AGENT_ROLES
    assert r.resolve_agent_role(
        r.AGENT_CODE, role_models={r.AGENT_CODE: "llm4decompile-33b"},
        resident_model="res") == "llm4decompile-33b"


def test_allowed_categories_are_within_the_manifest_taxonomy():
    for agent in t.AGENTS:
        assert t.spec(agent).allowed_categories <= set(m.CATEGORIES)


def test_bad_spec_fails_closed():
    with pytest.raises(t.TopologyError):
        t.AgentSpec("x", "not-a-role", frozenset(), frozenset(), "")
    with pytest.raises(t.TopologyError):
        t.AgentSpec("x", r.AGENT_FAST, frozenset({"not-a-category"}), frozenset(), "")


def test_unknown_agent_raises():
    with pytest.raises(t.TopologyError):
        t.spec("archivist")
    with pytest.raises(t.TopologyError):
        t.role_for("archivist")


# ---- filtered MCP tool access by role ------------------------------------
CATALOG = [
    {"name": "ghidra", "category": "decompiler"},
    {"name": "objdump", "category": "disassembler"},
    {"name": "binwalk", "category": "firmware"},
    {"name": "gdb", "category": "debugger"},
    {"name": "afl", "category": "fuzzer"},
    {"name": "flashrom", "category": "flash-programmer"},
    {"name": "hackrf", "category": "sdr"},
    {"name": "tshark", "category": "network"},
    {"name": "mystery", "category": "not-a-real-category"},
    {"name": "unlabeled"},  # no category at all
]


def _names(records):
    return {rec["name"] for rec in records}


def test_static_sees_only_static_tools():
    got = _names(t.tools_for(t.STATIC, CATALOG))
    assert got == {"ghidra", "objdump", "binwalk"}
    # the partition: a static worker can never reach a dynamic instrument.
    assert "gdb" not in got and "afl" not in got and "flashrom" not in got


def test_dynamic_sees_only_dynamic_tools():
    got = _names(t.tools_for(t.DYNAMIC, CATALOG))
    assert got == {"gdb", "afl", "flashrom", "hackrf", "tshark"}
    assert "ghidra" not in got and "objdump" not in got


def test_static_and_dynamic_toolsets_are_disjoint():
    static = _names(t.tools_for(t.STATIC, CATALOG))
    dynamic = _names(t.tools_for(t.DYNAMIC, CATALOG))
    assert static.isdisjoint(dynamic)


def test_supervisor_runs_no_bench_tool():
    assert t.tools_for(t.SUPERVISOR, CATALOG) == []


def test_filter_fails_closed_on_unknown_or_missing_category():
    # neither worker is ever handed the uncategorized / bad-category tools.
    for agent in (t.STATIC, t.DYNAMIC):
        got = _names(t.tools_for(agent, CATALOG))
        assert "mystery" not in got
        assert "unlabeled" not in got


# ---- delegation ----------------------------------------------------------
def test_only_supervisor_delegates_to_workers():
    assert t.can_delegate(t.SUPERVISOR, t.STATIC)
    assert t.can_delegate(t.SUPERVISOR, t.DYNAMIC)
    assert not t.can_delegate(t.STATIC, t.DYNAMIC)
    assert not t.can_delegate(t.DYNAMIC, t.STATIC)
    assert not t.can_delegate(t.STATIC, t.SUPERVISOR)


def test_delegate_to_unknown_raises():
    with pytest.raises(t.TopologyError):
        t.can_delegate(t.SUPERVISOR, "archivist")


# ---- confidence-based iterative loop -------------------------------------
def test_accept_at_or_above_threshold():
    d = t.next_action(t.STATIC, 0.7)
    assert d.action == t.ACCEPT and d.next_approach is None and d.escalate_to is None
    assert t.next_action(t.STATIC, 0.95).action == t.ACCEPT


def test_low_confidence_retries_an_untried_alternative():
    d = t.next_action(
        t.STATIC, 0.4,
        approaches_tried=["ghidra-auto"],
        approaches_available=["ghidra-auto", "manual-prologue-scan", "strings-xref"])
    assert d.action == t.RETRY_ALTERNATIVE
    assert d.next_approach == "manual-prologue-scan"
    assert d.escalate_to is None


def test_worker_escalates_to_supervisor_when_out_of_approaches():
    d = t.next_action(
        t.DYNAMIC, 0.3,
        approaches_tried=["qemu-user", "qemu-system"],
        approaches_available=["qemu-user", "qemu-system"])
    assert d.action == t.ESCALATE
    assert d.escalate_to == t.SUPERVISOR


def test_supervisor_escalates_to_operator_not_itself():
    d = t.next_action(t.SUPERVISOR, 0.3, approaches_tried=["a"], approaches_available=["a"])
    assert d.action == t.ESCALATE
    assert d.escalate_to == t.OPERATOR


def test_no_alternatives_at_all_escalates():
    # confidence below bar and an empty approach inventory -> straight to escalate.
    assert t.next_action(t.STATIC, 0.1).action == t.ESCALATE


def test_custom_threshold_can_flip_the_decision():
    assert t.next_action(t.STATIC, 0.6, threshold=0.5).action == t.ACCEPT
    assert t.next_action(t.STATIC, 0.6, threshold=0.9,
                         approaches_available=["b"]).action == t.RETRY_ALTERNATIVE


def test_confidence_out_of_range_fails_closed():
    for bad in (-0.1, 1.1, "high", None):
        with pytest.raises(t.TopologyError):
            t.next_action(t.STATIC, bad)


def test_next_action_validates_agent():
    with pytest.raises(t.TopologyError):
        t.next_action("archivist", 0.9)
