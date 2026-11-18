#!/usr/bin/env python3
"""Tests for wq#4913 — maksoor rea-guard bundle."""
import json
import os
import subprocess
import tempfile

BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))


def test_rea_guard_blocks_self_update():
    guard = os.path.join(BUNDLE_DIR, "rea-guard")
    for subcmd in ("update", "self-update", "upgrade"):
        p = subprocess.run(
            ["bash", guard, subcmd],
            capture_output=True, text=True,
            env={**os.environ, "WQ4913_REAL_REA": "/bin/true"},
        )
        assert p.returncode == 1, f"rea-guard should block '{subcmd}', got rc={p.returncode}"
        assert "blocked" in p.stderr.lower(), f"rea-guard should say blocked for '{subcmd}'"


def test_rea_guard_passes_mcp():
    guard = os.path.join(BUNDLE_DIR, "rea-guard")
    p = subprocess.run(
        ["bash", guard, "mcp"],
        capture_output=True, text=True,
        env={**os.environ, "WQ4913_REAL_REA": "/bin/true"},
    )
    assert p.returncode == 0, f"rea-guard should pass 'mcp' through, got rc={p.returncode}"


def test_patch_mcp_json():
    mcp_config = {
        "mcpServers": {
            "rea": {"command": "rea-guard", "args": ["mcp"]},
            "rea-maksoor": {
                "command": "ssh",
                "args": [
                    "maksoor@100.81.204.74",
                    "/home/maksoor/.npm-global/bin/rea",
                    "mcp",
                ],
            },
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(mcp_config, f)
        tmp = f.name
    try:
        p = subprocess.run(
            [
                "python3", os.path.join(BUNDLE_DIR, "patch_mcp_json.py"),
                tmp, "/home/maksoor/re/bin/rea-guard",
            ],
            capture_output=True, text=True,
        )
        assert p.returncode == 0, f"patch_mcp_json.py failed: {p.stderr}"
        with open(tmp) as fh:
            result = json.load(fh)
        args = result["mcpServers"]["rea-maksoor"]["args"]
        assert args[1] == "/home/maksoor/re/bin/rea-guard", f"args[1] not patched: {args}"
        assert args[0] == "maksoor@100.81.204.74", f"args[0] changed: {args}"
        assert args[2] == "mcp", f"args[2] changed: {args}"
    finally:
        os.unlink(tmp)


def test_patch_mcp_json_missing_entry():
    mcp_config = {"mcpServers": {"rea": {"command": "rea-guard", "args": ["mcp"]}}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(mcp_config, f)
        tmp = f.name
    try:
        p = subprocess.run(
            [
                "python3", os.path.join(BUNDLE_DIR, "patch_mcp_json.py"),
                tmp, "/home/maksoor/re/bin/rea-guard",
            ],
            capture_output=True, text=True,
        )
        assert p.returncode == 1, "should fail when rea-maksoor is missing"
    finally:
        os.unlink(tmp)


def test_template_routes_through_guard():
    template_path = os.path.join(
        BUNDLE_DIR, "..", "wq4006-rea-security-rescope-a1", "rea.mcp.json.project"
    )
    with open(template_path) as fh:
        config = json.load(fh)
    args = config["mcpServers"]["rea-maksoor"]["args"]
    assert "rea-guard" in args[1], (
        f"rea.mcp.json.project should route rea-maksoor through rea-guard, got: {args[1]}"
    )
