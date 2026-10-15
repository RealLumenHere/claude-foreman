"""wq#3970 -- tests for the target-class -> toolset routing table.

Loads the real, committed ``routing.json`` and ``tool-catalog.json`` (not a
synthetic fixture): the acceptance criteria is that this shipped table is
internally valid and every tool id it names actually resolves in the shipped
catalog, so the thing under test has to be the real files.
"""

from limpet.registry import routing


def _table():
    return routing.load_routing_table()


# --- schema validation -----------------------------------------------------

def test_shipped_routing_table_is_schema_valid():
    assert routing.validate_routing_table(_table()) == []


def test_validate_rejects_non_mapping():
    assert routing.validate_routing_table([]) != []


def test_validate_rejects_missing_target_classes():
    assert routing.validate_routing_table({"schema_version": 1}) != []


def test_validate_rejects_empty_toolset_groups():
    errors = routing.validate_routing_table({
        "schema_version": 1,
        "target_classes": {"pcap": {"description": "x", "toolset_groups": []}},
    })
    assert any("toolset_groups" in e for e in errors)


def test_validate_rejects_missing_stage_and_empty_tools():
    errors = routing.validate_routing_table({
        "schema_version": 1,
        "target_classes": {
            "pcap": {
                "description": "x",
                "toolset_groups": [{"tools": []}],
            }
        },
    })
    assert any("stage" in e for e in errors)
    assert any("tools" in e for e in errors)


def test_validate_rejects_duplicate_stage():
    errors = routing.validate_routing_table({
        "schema_version": 1,
        "target_classes": {
            "pcap": {
                "description": "x",
                "toolset_groups": [
                    {"stage": "extract", "tools": ["unblob"]},
                    {"stage": "extract", "tools": ["binwalk"]},
                ],
            }
        },
    })
    assert any("duplicate stage" in e for e in errors)


# --- every routed tool resolves in tool-catalog.json ------------------------

def test_every_routed_tool_resolves_in_tool_catalog():
    table = _table()
    catalog_names = routing.load_catalog_tool_names()
    assert routing.validate_tools_resolve(table, catalog_names) == []


def test_validate_tools_resolve_flags_unknown_tool():
    table = {
        "schema_version": 1,
        "target_classes": {
            "pcap": {
                "description": "x",
                "toolset_groups": [{"stage": "protocol-analysis", "tools": ["not-a-real-tool"]}],
            }
        },
    }
    errors = routing.validate_tools_resolve(table, {"netzob"})
    assert any("not-a-real-tool" in e for e in errors)


# --- router resolution for fixture target classes ---------------------------

def test_router_resolves_rockchip_firmware():
    groups = routing.resolve_target_class(_table(), "rockchip-firmware")
    stages = {g["stage"]: g["tools"] for g in groups}
    assert stages["flash-debug"] == ["rkdeveloptool"]
    assert "ghidra" in stages["disassemble"]


def test_router_resolves_android_apk():
    groups = routing.resolve_target_class(_table(), "android-apk")
    stages = {g["stage"]: g["tools"] for g in groups}
    assert stages["static"] == ["apktool", "jadx"]


def test_router_resolves_pcap():
    groups = routing.resolve_target_class(_table(), "pcap")
    assert groups == [{"stage": "protocol-analysis", "tools": ["netzob"]}]


def test_router_unknown_target_class_fails_closed():
    import pytest
    with pytest.raises(routing.RoutingError):
        routing.resolve_target_class(_table(), "not-a-real-target-class")
