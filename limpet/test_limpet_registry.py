"""Integration tests for the Registry over a real (temporary) plugin tree.

Falsifiability (wq#2630/wq#3810 probe doctrine): the Registry's scope is its
plugins_dir / state_dir, whose DEFAULTS are /etc/limpet/plugins.d and
/var/lib/limpet. Every test here pins both to a tmp_path OUTSIDE those
defaults, so the suite exercises the override boundary rather than confirming
only the built-in paths -- a drop-in that failed to load from a non-default
dir would fail these tests, it is not drawn from the default population."""
import json

import pytest

from registry import Registry

GHIDRA = """
[plugin]
name = "ghidra"
version = "11.2"
kind = "tool"
[health]
command = "true"
"""

MCP = """
[plugin]
name = "ghidra-mcp"
version = "0.3.1"
kind = "tool"
[dependencies]
ghidra = ">=11.0"
[health]
command = "true"
"""


@pytest.fixture
def tree(tmp_path):
    plugins = tmp_path / "plugins.d"
    state = tmp_path / "state"
    plugins.mkdir()
    # health_runner stubbed healthy so tests never shell out
    reg = Registry(plugins_dir=str(plugins), state_dir=str(state),
                   health_runner=lambda cmd, t: (0, "ok"), clock=lambda: 1000.0)
    return reg, plugins, state


def drop(plugins_dir, name, body):
    (plugins_dir / f"{name}.toml").write_text(body)


def test_scan_loads_valid_and_rejects_bad(tree):
    reg, plugins, _ = tree
    drop(plugins, "ghidra", GHIDRA)
    (plugins / "broken.toml").write_text('[plugin]\nname="broken"\n')  # no version/kind
    report = reg.scan()
    assert set(report.loaded) == {"ghidra"}
    assert any("broken.toml" in p for p in report.rejected)


def test_filename_must_match_plugin_name(tree):
    reg, plugins, _ = tree
    drop(plugins, "wrongname", GHIDRA)  # body says name="ghidra"
    report = reg.scan()
    assert report.loaded == {}
    assert any("must match the drop-in filename stem" in e
               for errs in report.rejected.values() for e in errs)


def test_install_defaults_to_enabled_and_reconciles(tree):
    reg, plugins, state = tree
    drop(plugins, "ghidra", GHIDRA)
    unified = reg.reconcile()
    assert unified["plugins"]["ghidra"]["state"] == "active"
    # unified.json written atomically and readable
    on_disk = json.loads((state / "unified.json").read_text())
    assert on_disk["start_order"] == ["ghidra"]


def test_disable_persists_and_blocks_dependant(tree):
    reg, plugins, _ = tree
    drop(plugins, "ghidra", GHIDRA)
    drop(plugins, "ghidra-mcp", MCP)
    assert reg.reconcile()["plugins"]["ghidra-mcp"]["state"] == "active"
    reg.disable("ghidra")
    unified = reg.reconcile()
    assert unified["plugins"]["ghidra"]["state"] == "disabled"
    assert unified["plugins"]["ghidra-mcp"]["state"] == "blocked"
    # re-enable restores both
    reg.enable("ghidra")
    assert reg.reconcile()["plugins"]["ghidra-mcp"]["state"] == "active"


def test_missing_dependency_leaves_mcp_blocked(tree):
    reg, plugins, _ = tree
    drop(plugins, "ghidra-mcp", MCP)  # ghidra not installed
    unified = reg.reconcile()
    assert unified["plugins"]["ghidra-mcp"]["state"] == "blocked"
    assert "not installed" in unified["plugins"]["ghidra-mcp"]["reason"]


def test_unhealthy_dependency_flagged_not_started(tree, tmp_path):
    plugins = tmp_path / "p2"
    state = tmp_path / "s2"
    plugins.mkdir()
    reg = Registry(plugins_dir=str(plugins), state_dir=str(state),
                   health_runner=lambda cmd, t: (1, "device absent"), clock=lambda: 1.0)
    drop(plugins, "ghidra", GHIDRA)
    unified = reg.reconcile()
    assert unified["plugins"]["ghidra"]["state"] == "unhealthy"
    assert unified["start_order"] == []


def test_conflicting_plugins_second_one_blocked(tree):
    reg, plugins, _ = tree
    drop(plugins, "hackrf", '[plugin]\nname="hackrf"\nversion="1.0"\nkind="hardware"\n'
         'category="sdr"\nconflicts=["hackrf-portapack"]\n')
    drop(plugins, "hackrf-portapack",
         '[plugin]\nname="hackrf-portapack"\nversion="1.0"\nkind="hardware"\ncategory="sdr"\n')
    unified = reg.reconcile()
    assert unified["plugins"]["hackrf"]["state"] == "active"
    assert unified["plugins"]["hackrf-portapack"]["state"] == "blocked"
    assert "conflicts with 'hackrf'" in unified["plugins"]["hackrf-portapack"]["reason"]


def test_empty_plugins_dir_is_clean(tree):
    reg, _, _ = tree
    unified = reg.reconcile()
    assert unified["plugins"] == {}
    assert unified["start_order"] == []
