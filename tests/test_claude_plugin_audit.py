"""wq#4883: claude_plugin_audit distinguishes on-disk plugin dirs from loaded plugins."""
import importlib.util
import json
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "scripts" / "claude_plugin_audit.py"
_spec = importlib.util.spec_from_file_location("claude_plugin_audit", _SRC)
cpa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpa)


def _plugin(d: Path, name: str, hooks=None):
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
    (d / "hooks").mkdir()
    (d / "hooks" / "hooks.json").write_text(json.dumps(hooks if hooks is not None else {}))


def _home(tmp_path):
    cd = tmp_path / ".claude"
    (cd / "plugins").mkdir(parents=True)
    (cd / "settings.json").write_text("{}")
    cj = tmp_path / ".claude.json"
    cj.write_text(json.dumps({"pluginUsage": {"github@synced": {}}}))
    return cd, cj


def test_lumen_shape_copied_dirs_are_orphans(tmp_path):
    """The Lumen 2026-11-22 layout: plugins copied into plugins/blerbz/, no registry."""
    cd, cj = _home(tmp_path)
    blerbz = cd / "plugins" / "blerbz"
    _plugin(blerbz / "inference-continuez", "inference-continuez")
    _plugin(blerbz / "inference-confidenz", "inference-confidenz",
            {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}})
    _plugin(blerbz / "inference-planz", "inference-planz",
            {"hooks": {"PreToolUse": [{"matcher": {"tool_name": "Task"}, "hooks": []}]}})
    rep = cpa.audit(cd, cj)
    names = {o["name"]: o for o in rep["orphans"]}
    assert set(names) == {"inference-continuez", "inference-confidenz", "inference-planz"}
    assert rep["installed_plugins_json"] is False
    assert names["inference-continuez"]["hooks"]["hooks_json"] == "empty"
    assert names["inference-confidenz"]["hooks"]["events"] == ["Stop"]
    assert names["inference-planz"]["hooks"]["problems"]  # object matcher flagged
    assert not any(o["ever_loaded"] for o in rep["orphans"])
    assert cpa.main(["--claude-dir", str(cd), "--claude-json", str(cj)]) == 1


def test_registered_plugin_is_not_orphan(tmp_path):
    """Probe that can fail: a properly installed plugin must NOT be reported."""
    cd, cj = _home(tmp_path)
    p = cd / "plugins" / "local-src" / "foo"
    _plugin(p, "foo")
    (cd / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"foo@local": [{"scope": "user", "installPath": str(p)}]}}))
    (cd / "settings.json").write_text(json.dumps({"enabledPlugins": {"foo@local": True}}))
    rep = cpa.audit(cd, cj)
    assert rep["orphans"] == []
    assert rep["installed"] == ["foo@local"]
    assert cpa.main(["--claude-dir", str(cd), "--claude-json", str(cj)]) == 0


def test_claude_managed_subtrees_ignored(tmp_path):
    cd, cj = _home(tmp_path)
    _plugin(cd / "plugins" / "synced" / "org_user" / "abc", "github")
    _plugin(cd / "plugins" / "marketplaces" / "official" / "plugins" / "x", "x")
    assert cpa.audit(cd, cj)["orphans"] == []
