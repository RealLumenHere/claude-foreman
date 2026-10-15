"""Tests for the wq#3935 Rockchip MCP tool: the [[wrappers]] manifest, its
validator/renderer, and the limpet-plugin.toml drop-in.

The committed manifests are the canonical data under test (same posture as
limpet/test_target_devices.py). The meta-server's own registry parser is loaded
by file path under a private module name so this test proves the rendered JSON
is accepted by the real consumer, without colliding with limpet/registry.py in
sys.modules.
"""
import copy
import importlib.util
import os
import pathlib
import sys

import pytest

HERE = pathlib.Path(os.path.dirname(os.path.abspath(__file__)))
LIMPET_DIR = HERE.parent.parent
REPO_ROOT = LIMPET_DIR.parent

sys.path.insert(0, str(HERE))

import mcp_wrappers as mw  # noqa: E402


def _load_by_path(module_name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves field types via sys.modules[cls.__module__], so the
    # module must be registered before exec, not just returned
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_committed_manifest_is_valid_with_exactly_the_ticketed_wrappers():
    data = mw.load_manifest()
    assert mw.validate_manifest(data) == []
    names = [w["name"] for w in data["wrappers"]]
    assert names == ["list_devices", "read_partition_table", "read_flash", "write_flash"]
    assert data["tool"]["name"] == "rockchip"


def test_render_is_accepted_by_the_meta_server_registry_parser():
    ms_dir = REPO_ROOT / "ops" / "wq3808-limpet-mcp-meta-server-a1"
    # registry.py does `from stdio_mcp_client import ...`; load that sibling
    # under its canonical name first (same file the meta-server's own tests
    # import, so a shared sys.modules entry is compatible, not a collision)
    if "stdio_mcp_client" not in sys.modules:
        _load_by_path("stdio_mcp_client", ms_dir / "stdio_mcp_client.py")
    ms_registry = _load_by_path("wq3935_ms_registry", ms_dir / "registry.py")
    entries = mw.render_cli_tools(mw.load_manifest())
    parsed = [ms_registry._parse_cli_tool(e) for e in entries]
    assert [p.name for p in parsed] == [
        "rockchip_list_devices", "rockchip_read_partition_table",
        "rockchip_read_flash", "rockchip_write_flash"]
    # every placeholder in every command is a whole argv element declared in
    # the schema -- the substitution contract cli_wrapper.py relies on
    for p in parsed:
        properties = p.args_schema.get("properties", {})
        for part in p.command:
            if part.startswith("{") and part.endswith("}"):
                assert part[1:-1] in properties


def test_destructive_wrapper_renders_disabled_and_reads_render_enabled():
    entries = {e["name"]: e for e in mw.render_cli_tools(mw.load_manifest())}
    assert entries["rockchip_write_flash"]["enabled"] is False
    assert entries["rockchip_read_flash"]["enabled"] is True
    assert entries["rockchip_list_devices"]["enabled"] is True


# Probes from OUTSIDE the committed manifest (wq#2630: an acceptance probe must
# be able to fail): mutated copies that each violate one validation rule.

def _mutated(mutate):
    data = copy.deepcopy(mw.load_manifest())
    mutate(data)
    return data


def test_undeclared_placeholder_is_rejected():
    def mutate(data):
        data["wrappers"][2]["command"].append("{made_up_arg}")
    errors = mw.validate_manifest(_mutated(mutate))
    assert any("made_up_arg" in e for e in errors)


def test_embedded_placeholder_is_rejected():
    def mutate(data):
        data["wrappers"][2]["command"][-1] = "--out={out_path}"
    errors = mw.validate_manifest(_mutated(mutate))
    assert any("whole argv" in e for e in errors)


def test_missing_destructive_flag_is_rejected():
    def mutate(data):
        del data["wrappers"][3]["destructive"]
    errors = mw.validate_manifest(_mutated(mutate))
    assert any("destructive" in e for e in errors)


def test_render_refuses_an_invalid_manifest():
    def mutate(data):
        data["wrappers"][0]["timeout_sec"] = -1
    with pytest.raises(ValueError):
        mw.render_cli_tools(_mutated(mutate))


def test_limpet_plugin_manifest_parses_against_the_real_schema():
    limpet_manifest = _load_by_path("wq3935_limpet_manifest", LIMPET_DIR / "manifest.py")
    text = (HERE / "limpet-plugin.toml").read_text()
    plugin = limpet_manifest.parse_manifest(text)
    assert plugin.name == "rockchip-tools"
    assert plugin.kind == "tool"
    assert plugin.health is not None
    assert plugin.health.command == "/usr/local/sbin/rkdeveloptool ver"
