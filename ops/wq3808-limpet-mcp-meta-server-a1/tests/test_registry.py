import json
import time

import pytest

from registry import ManifestError, ManifestWatcher, load_manifest


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_load_manifest_parses_both_kinds(tmp_path):
    path = tmp_path / "manifest.json"
    _write(
        path,
        {
            "mcp_servers": [
                {"name": "rea", "transport": "stdio", "command": ["rea", "mcp"], "enabled": False}
            ],
            "cli_tools": [
                {"name": "file", "command": ["file", "{path}"], "description": "identify a file"}
            ],
        },
    )
    manifest = load_manifest(str(path))
    assert manifest.mcp_servers[0].name == "rea"
    assert manifest.mcp_servers[0].enabled is False
    assert manifest.cli_tools[0].name == "file"
    assert manifest.cli_tools[0].timeout_sec == 30.0


def test_duplicate_names_rejected(tmp_path):
    path = tmp_path / "manifest.json"
    _write(
        path,
        {
            "mcp_servers": [{"name": "dup", "transport": "stdio", "command": ["x"]}],
            "cli_tools": [{"name": "dup", "command": ["y"]}],
        },
    )
    with pytest.raises(ManifestError, match="duplicate"):
        load_manifest(str(path))


def test_stdio_server_without_command_rejected(tmp_path):
    path = tmp_path / "manifest.json"
    _write(path, {"mcp_servers": [{"name": "broken", "transport": "stdio"}]})
    with pytest.raises(ManifestError, match="no 'command'"):
        load_manifest(str(path))


def test_watcher_detects_change(tmp_path):
    path = tmp_path / "manifest.json"
    _write(path, {"cli_tools": [{"name": "a", "command": ["echo"]}]})
    watcher = ManifestWatcher(str(path))
    assert watcher.poll() is None  # unchanged

    time.sleep(0.01)
    _write(path, {"cli_tools": [{"name": "a", "command": ["echo"]}, {"name": "b", "command": ["cat"]}]})

    reloaded = watcher.poll()
    assert reloaded is not None
    assert [t.name for t in reloaded.cli_tools] == ["a", "b"]
    assert watcher.poll() is None  # settles after one reload
