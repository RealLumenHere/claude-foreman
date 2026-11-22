"""wq#4933: claude_context.sh host guard.

Commander calls once landed on two hosts in one session (two live replicas of the mcp-horus
tunnel, each with its own DC). The boot script now prints which host answered and warns when
it is not the expected carrier. These tests stub `hostname` on PATH so the guard sees a host
other than the one the test runs on; the non-carrier case is the one that has to fail loudly.

Run: python3 -m pytest -q ops/wq4790-claude-context-sh-into-bin/test_host_guard.py
"""
import os
import pathlib
import stat
import subprocess

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "claude_context.sh"


def _run(tmp_path, fake_host, *args, expect=None):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "hostname"
    stub.write_text("#!/bin/sh\necho %s\n" % fake_host)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ)
    env["PATH"] = "%s:%s" % (bindir, env.get("PATH", "/usr/bin:/bin"))
    env.pop("CLAUDE_CONTEXT_EXPECT_HOST", None)
    if expect is not None:
        env["CLAUDE_CONTEXT_EXPECT_HOST"] = expect
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_carrier_host_prints_ok_line(tmp_path):
    r = _run(tmp_path, "lumen", "--host-check")
    assert r.returncode == 0
    first = r.stdout.splitlines()[0]
    assert first.startswith("===== HOST: lumen (user ")
    assert "WARN" not in r.stdout


@pytest.mark.parametrize("host", ["horus", "seneca", "lumen-1", "neith"])
def test_non_carrier_host_warns(tmp_path, host):
    r = _run(tmp_path, host, "--host-check")
    assert r.returncode == 0  # warn, never refuse: a failover replica still needs its boot
    first = r.stdout.splitlines()[0]
    assert first.startswith("===== WARN HOST: %s " % host)
    assert "NOT the expected carrier 'lumen'" in first
    assert "ssh-wrap every Lumen command" in r.stdout
    assert "verify_topology.py" in r.stdout


def test_expect_override_flips_verdict(tmp_path):
    r = _run(tmp_path, "horus", "--host-check", expect="horus")
    assert r.stdout.startswith("===== HOST: horus ")
    r = _run(tmp_path, "lumen", "--host-check", expect="horus")
    assert r.stdout.startswith("===== WARN HOST: lumen ")


def test_core_prints_guard_before_anything_else(tmp_path):
    # --core's first output must be the host line, ahead of the wq registration gate, so even
    # a REFUSED boot shows which machine answered. Checked statically: running --core for real
    # would hit the live wq/session-briefing renderers.
    src = SCRIPT.read_text()
    core = src.index('if [ "$1" = "--core" ]; then')
    after = src[core:].splitlines()[1].strip()
    assert after == "host_guard"
    # and the guard is defined before the cd that fails on a non-Lumen host
    assert src.index("host_guard() {") < src.index("cd /home/lumen/minerva/context")


def test_live_latest_md_block_is_tracked():
    # the LATEST.md block was live-only drift; installing this copy must not drop it
    assert "fleet-intel/sessions/LATEST.md" in SCRIPT.read_text()


# wq#5246 deploy-verify: install.sh puts this copy at /home/lumen/bin/claude_context.sh. The
# checks below read the LIVE path, so a drifted or un-deployed live copy fails them (they skip
# only off-Lumen, where the live path does not exist).
LIVE = pathlib.Path(os.environ.get("CLAUDE_CONTEXT_DST", "/home/lumen/bin/claude_context.sh"))
_live = pytest.mark.skipif(not LIVE.exists(), reason="live claude_context.sh not on this host")


@_live
def test_live_copy_matches_tracked():
    assert LIVE.read_bytes() == SCRIPT.read_bytes(), (
        "live %s drifted from tracked copy; run install.sh" % LIVE)


@_live
def test_live_host_check_on_carrier(tmp_path):
    # run the LIVE script (not the tracked one) with hostname pinned to the carrier
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "hostname"
    stub.write_text("#!/bin/sh\necho lumen\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin")))
    env.pop("CLAUDE_CONTEXT_EXPECT_HOST", None)
    r = subprocess.run(["bash", str(LIVE), "--host-check"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert r.stdout.splitlines()[0].startswith("===== HOST: lumen (user ")


@_live
def test_live_core_ends_with_latest_session_block():
    # --core's last section is still the LATEST SESSION block (static: a real --core run hits
    # live wq/session-briefing renderers)
    src = LIVE.read_text()
    core = src[src.index('if [ "$1" = "--core" ]; then'):]
    tail = core[core.rindex("===== LATEST SESSION"):]
    assert "===== END LATEST SESSION =====" in tail
    assert "=====" not in tail.split("===== END LATEST SESSION =====", 1)[1]
