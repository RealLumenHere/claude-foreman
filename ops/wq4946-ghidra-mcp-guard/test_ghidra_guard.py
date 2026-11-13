"""wq#4946: the generated ghidra-lumen launcher, run against the real bridge, hides and refuses what it should.

Uses this branch's lumen_mcp_guard.py (substituted for the installed one in the printed launcher) and
GHIDRA_MCP_AUTOSTART=0, so no Ghidra is started and nothing is loaded (see probe_guarded.py). Needs /opt/ghidra-mcp and the
shared guard venv on Lumen; skipped elsewhere.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALL = os.path.join(HERE, "install.sh")
REPO_GUARD = os.path.join(HERE, "..", "wq4514-mcp-install-batch", "lumen_mcp_guard.py")
LIVE_GUARD = "/home/lumen/.local/share/mcp/_guard/lumen_mcp_guard.py"
LIVE_PINS = "/home/lumen/.local/share/mcp/ghidra-lumen/pins.json"
BUNDLE_PINS = os.path.join(HERE, "ghidra-pins.json")
GPY = "/home/lumen/.local/share/mcp/_guard/venv/bin/python"
BRIDGE = "/opt/ghidra-mcp/bridge-stdio.sh"
ALLOW = [l for l in open(os.path.join(HERE, "ghidra-allow.txt")).read().split() if not l.startswith("#")]
NEVER = {"run_script_inline", "run_ghidra_script", "connect_instance", "search_tools", "load_tool_group",
         "export_program", "archive_project", "restore_project", "checkin_program", "archive_ingest_program"}

live = pytest.mark.skipif(not (os.path.exists(BRIDGE) and os.path.exists(GPY)), reason="not on Lumen")


def launcher():
    p = subprocess.run(["bash", INSTALL, "--print-launcher"], capture_output=True, text=True, timeout=30)
    assert p.returncode == 0, p.stderr
    return p.stdout


def test_allowlist_is_explicit_and_excludes_execution_and_offhost_tools():
    assert len(ALLOW) == len(set(ALLOW)) == 202
    assert not NEVER & set(ALLOW)


def test_launcher_pins_paths_masks_output_and_wraps_the_bridge():
    text = launcher()
    assert f"--allow {','.join(ALLOW)} " in text
    for pin in ("load_program:file", "import_file:file_path", "open_project:path"):
        assert f"--path-arg {pin}=/home/lumen/re " in text
    assert "--mask-output /home/lumen/bin/mask-secrets" in text and "GHIDRA_MCP_BRIDGE_ARGS=--no-lazy" in text
    assert f"--pins {LIVE_PINS} " in text
    pins = json.load(open(BUNDLE_PINS))["servers"]["ghidra-lumen@guarded"]["tools"]
    assert sorted(pins) == sorted(ALLOW)
    assert text.rstrip().endswith(f"-- {BRIDGE}")
    assert subprocess.run(["bash", "-n"], input=text, text=True).returncode == 0


@live
def test_guarded_bridge_hides_and_denies(tmp_path):
    path = tmp_path / "launch.sh"
    # 202 listed also proves every pin matches the live bridge: a drifted tool would be hidden.
    path.write_text(launcher().replace(LIVE_GUARD, os.path.abspath(REPO_GUARD)).replace(LIVE_PINS, BUNDLE_PINS))
    path.chmod(0o700)
    env = dict(os.environ, GHIDRA_MCP_AUTOSTART="0", HOME=str(tmp_path))
    p = subprocess.run([GPY, "-I", os.path.join(HERE, "probe_guarded.py"), str(path)],
                       capture_output=True, text=True, timeout=300, env=env)
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-500:]
    assert f"tools/list: {len(ALLOW)} tools" in p.stdout
    rows = [json.loads(l) for l in (tmp_path / ".local/state/lumen-mcp-guard/ghidra-lumen.jsonl").read_text().splitlines()]
    denied = {r["tool"] for r in rows if r["decision"] == "DENY"}
    assert {"run_script_inline", "connect_instance", "load_program", "import_file", "open_project"} <= denied


@live
def test_backend_still_offers_every_allowlisted_tool():
    env = dict(os.environ, GHIDRA_MCP_AUTOSTART="0")
    p = subprocess.run([GPY, "-I", REPO_GUARD, "--list-backend", "--allow", ",".join(ALLOW),
                        "--env", "GHIDRA_MCP_BRIDGE_ARGS=--no-lazy", "--", BRIDGE],
                       capture_output=True, text=True, timeout=180, env=env)
    rep = json.loads(p.stdout)
    assert p.returncode == 0 and rep["missing_upstream"] == []
    assert NEVER <= set(rep["hidden"])
