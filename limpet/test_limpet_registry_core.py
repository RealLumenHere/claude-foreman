"""Unit tests for registry_core.resolve: enable/disable, dependency
satisfaction, missing/disabled/version-mismatch cascades, cycle detection,
unhealthy flagging, and topological start order. (wq#3810 steps 4-6.)"""
from manifest import HealthCheck, PluginManifest
import registry_core as rc


def mk(name, version="1.0", kind="tool", deps=None, health=False, conflicts=None,
       category="", optional_deps=None):
    return PluginManifest(
        name=name, version=version, kind=kind, category=category,
        dependencies=deps or {}, conflicts=conflicts or [],
        optional_dependencies=optional_deps or {},
        health=HealthCheck("probe") if health else None)


def test_active_when_enabled_and_no_deps():
    man = {"a": mk("a")}
    res = rc.resolve(man, enabled={"a"})
    assert res.plugins["a"].state == "active"
    assert res.start_order == ["a"]


def test_disabled_plugin_is_disabled():
    man = {"a": mk("a")}
    res = rc.resolve(man, enabled=set())
    assert res.plugins["a"].state == "disabled"
    assert res.start_order == []


def test_missing_dependency_blocks():
    man = {"mcp": mk("mcp", deps={"ghidra": ">=11.0"})}
    res = rc.resolve(man, enabled={"mcp"})
    assert res.plugins["mcp"].state == "blocked"
    assert "not installed" in res.plugins["mcp"].reason


def test_disabled_dependency_blocks_dependant():
    man = {"ghidra": mk("ghidra", "11.2"), "mcp": mk("mcp", deps={"ghidra": ">=11.0"})}
    res = rc.resolve(man, enabled={"mcp"})  # ghidra installed but NOT enabled
    assert res.plugins["mcp"].state == "blocked"
    assert "disabled" in res.plugins["mcp"].reason


def test_version_mismatch_blocks():
    man = {"ghidra": mk("ghidra", "10.9"), "mcp": mk("mcp", deps={"ghidra": ">=11.0"})}
    res = rc.resolve(man, enabled={"ghidra", "mcp"})
    assert res.plugins["mcp"].state == "blocked"
    assert "not satisfied" in res.plugins["mcp"].reason


def test_satisfied_dependency_activates_both_in_order():
    man = {"ghidra": mk("ghidra", "11.2"), "mcp": mk("mcp", deps={"ghidra": ">=11.0"})}
    res = rc.resolve(man, enabled={"ghidra", "mcp"})
    assert res.plugins["mcp"].state == "active"
    assert res.plugins["ghidra"].state == "active"
    # dependency must start before dependant
    assert res.start_order.index("ghidra") < res.start_order.index("mcp")


def test_unhealthy_when_health_failing():
    man = {"a": mk("a", health=True)}
    res = rc.resolve(man, enabled={"a"}, health_ok={"a": False})
    assert res.plugins["a"].state == "unhealthy"
    # unhealthy plugins are not in the start order
    assert res.start_order == []


def test_cycle_detected():
    man = {
        "a": mk("a", deps={"b": "*"}),
        "b": mk("b", deps={"a": "*"}),
    }
    res = rc.resolve(man, enabled={"a", "b"})
    assert res.plugins["a"].state == "cycle"
    assert res.plugins["b"].state == "cycle"
    assert res.start_order == []


def test_three_level_chain_orders_transitively():
    man = {
        "base": mk("base", "1.0"),
        "mid": mk("mid", "1.0", deps={"base": ">=1.0"}),
        "top": mk("top", "1.0", deps={"mid": ">=1.0"}),
    }
    res = rc.resolve(man, enabled={"base", "mid", "top"})
    order = res.start_order
    assert order.index("base") < order.index("mid") < order.index("top")


def test_mutual_conflict_blocks_the_second_one():
    man = {"a": mk("a", conflicts=["b"]), "b": mk("b")}
    res = rc.resolve(man, enabled={"a", "b"})
    assert res.plugins["a"].state == "active"
    assert res.plugins["b"].state == "blocked"
    assert "conflicts with 'a'" in res.plugins["b"].reason


def test_conflict_declared_one_sided_is_still_mutual():
    # only "a" declares the conflict; "b" must still be blocked by it.
    man = {"a": mk("a", conflicts=["b"]), "b": mk("b", conflicts=[])}
    res = rc.resolve(man, enabled={"a", "b"})
    assert res.plugins["b"].state == "blocked"


def test_conflict_with_disabled_plugin_does_not_block():
    man = {"a": mk("a", conflicts=["b"]), "b": mk("b")}
    res = rc.resolve(man, enabled={"a"})  # b not enabled -> no active conflict
    assert res.plugins["a"].state == "active"
    assert res.plugins["b"].state == "disabled"


def test_optional_dependency_satisfied_when_target_active():
    man = {"a": mk("a", optional_deps={"b": ">=1.0"}), "b": mk("b", "1.5")}
    res = rc.resolve(man, enabled={"a", "b"})
    assert res.plugins["a"].state == "active"  # never blocked by an optional dep
    assert res.plugins["a"].optional_dependencies_satisfied == {"b": True}


def test_optional_dependency_missing_does_not_block():
    man = {"a": mk("a", optional_deps={"b": ">=1.0"})}  # b not installed at all
    res = rc.resolve(man, enabled={"a"})
    assert res.plugins["a"].state == "active"
    assert res.plugins["a"].optional_dependencies_satisfied == {"b": False}


def test_optional_dependency_unsatisfied_version_reported_false():
    man = {"a": mk("a", optional_deps={"b": ">=2.0"}), "b": mk("b", "1.0")}
    res = rc.resolve(man, enabled={"a", "b"})
    assert res.plugins["a"].state == "active"
    assert res.plugins["a"].optional_dependencies_satisfied == {"b": False}


def test_optional_dependency_disabled_target_reported_false():
    man = {"a": mk("a", optional_deps={"b": "*"}), "b": mk("b")}
    res = rc.resolve(man, enabled={"a"})  # b installed but not enabled
    assert res.plugins["a"].state == "active"
    assert res.plugins["a"].optional_dependencies_satisfied == {"b": False}


def test_category_carried_through_resolution():
    man = {"a": mk("a", category="sdr")}
    res = rc.resolve(man, enabled={"a"})
    assert res.plugins["a"].category == "sdr"


def test_blocked_dependant_does_not_crash_order():
    # top depends on mid which is blocked (its dep missing) -> top also blocked,
    # neither in start order; base still active.
    man = {
        "base": mk("base"),
        "mid": mk("mid", deps={"absent": "*"}),
        "top": mk("top", deps={"mid": "*"}),
    }
    res = rc.resolve(man, enabled={"base", "mid", "top"})
    assert res.plugins["base"].state == "active"
    assert res.plugins["mid"].state == "blocked"
    assert res.plugins["top"].state == "blocked"
    assert res.start_order == ["base"]
