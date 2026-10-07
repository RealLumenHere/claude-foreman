"""wq#2500 — the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

RETIRED by wq#3486, so the sense is inverted: exit 0 when rsshub.service never
landed on Sia, exit 1 with a MISS if it did, exit 2 (ERR, never MISS) when Sia
cannot be reached. ssh is replaced by a fake on PATH; nothing touches Sia.
"""
import os
import stat
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent

FAKE_SSH = """#!/bin/sh
# last arg is the remote command: `true` for vremote_up, `test -e UNIT` for the check
for a; do last="$a"; done
[ "$FAKE_SIA_UP" = 1 ] || exit 255
case "$last" in
  true) exit 0 ;;
  "test -e "*) [ "$FAKE_UNIT_PRESENT" = 1 ] && exit 0; exit 1 ;;
esac
exit 99
"""


def run(tmp_path, *args, up=True, present=False, extra_env=None):
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    ssh = d / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(ssh.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{d}:/usr/bin:/bin",
               FAKE_SIA_UP="1" if up else "0",
               FAKE_UNIT_PRESENT="1" if present else "0", **(extra_env or {}))
    return subprocess.run(["bash", str(BUNDLE / "install.sh"), *args],
                          capture_output=True, text=True, env=env)


def test_handler_precedes_the_retired_refusal():
    text = (BUNDLE / "install.sh").read_text()
    assert text.index('if [ "${1:-}" = --verify ]; then') < text.index("WQ2500_FORCE_RETIRED_DEPLOY:-0")


def test_verify_ok_when_the_retired_unit_never_landed(tmp_path):
    proc = run(tmp_path, "--verify")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY OK" in proc.stdout


def test_verify_misses_when_the_retired_unit_is_on_sia(tmp_path):
    proc = run(tmp_path, "--verify", present=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "retired unit present on Sia" in proc.stdout


def test_unreachable_sia_is_err_not_miss(tmp_path):
    proc = run(tmp_path, "--verify", up=False)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ERR unreachable" in proc.stdout
    assert "MISS" not in proc.stdout


def test_install_refuses_without_the_force_flag(tmp_path):
    proc = run(tmp_path)
    assert proc.returncode == 2
    assert "RETIRED" in proc.stderr
