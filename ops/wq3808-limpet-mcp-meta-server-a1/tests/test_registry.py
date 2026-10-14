import json
import os
import time

import pytest

from registry import (
    DEFAULT_REQUEST_TIMEOUT_SEC,
    DEFAULT_STARTUP_TIMEOUT_SEC,
    ManifestError,
    ManifestWatcher,
    load_manifest,
)


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _one_server(**extra):
    entry = {"name": "rea", "transport": "stdio", "command": ["rea", "mcp"]}
    entry.update(extra)
    return {"mcp_servers": [entry], "cli_tools": []}


def test_child_timeouts_default_when_unset(tmp_path):
    path = tmp_path / "manifest.json"
    _write(path, _one_server())
    entry = load_manifest(str(path)).mcp_servers[0]
    assert entry.startup_timeout_sec == DEFAULT_STARTUP_TIMEOUT_SEC
    assert entry.request_timeout_sec == DEFAULT_REQUEST_TIMEOUT_SEC


def test_child_timeouts_are_read_from_the_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    _write(path, _one_server(startup_timeout_sec=3, request_timeout_sec="7.5"))
    entry = load_manifest(str(path)).mcp_servers[0]
    assert entry.startup_timeout_sec == 3.0
    assert entry.request_timeout_sec == 7.5


def test_shipped_example_manifest_parses():
    """The documented template is a real input, not a test fixture: it has to
    survive every validation rule the parser grows.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = load_manifest(os.path.join(os.path.dirname(here), "manifest.example.json"))
    by_name = {entry.name: entry for entry in manifest.mcp_servers}
    assert by_name["rea"].startup_timeout_sec == DEFAULT_STARTUP_TIMEOUT_SEC
    assert by_name["ghidra"].request_timeout_sec == 120.0


@pytest.mark.parametrize("bad", [0, -1, "nope"])
def test_unusable_child_timeout_is_a_manifest_error(tmp_path, bad):
    path = tmp_path / "manifest.json"
    _write(path, _one_server(startup_timeout_sec=bad))
    with pytest.raises(ManifestError):
        load_manifest(str(path))


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
