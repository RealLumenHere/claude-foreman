"""wq#3355: the bundle's `install.sh --verify` handler, per ops/VERIFY_CONTRACT.md.

The payload lands on nephthys as *user* units, so the handler is remote-only and must tell
"I could not look" (ERR, exit 2) apart from "it is not deployed" (MISS, exit 1) — wq#2985.
Both paths run here against a stub `ssh` on PATH, so the test needs no live node.
"""
import os
import subprocess
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
INSTALL = BUNDLE / "install.sh"

# Intercepts the two user-manager queries; everything else is run locally with HOME pointed
# at a directory that holds none of the payload, so each vremote_file reads as absent.
STUB_SSH = """#!/usr/bin/env bash
cmd="${!#}"
case "$cmd" in
  *systemctl*is-enabled*) printf %s "${STUB_ENABLED:-enabled}" ;;
  *systemctl*is-active*)  printf %s "${STUB_ACTIVE:-active}" ;;
  *)                      HOME="$STUB_HOME" exec bash -c "$cmd" ;;
esac
"""


def verify(env=None, node="stub-node"):
    e = dict(os.environ, CHAT_DIGEST_NODE=node, **(env or {}))
    return subprocess.run(["bash", str(INSTALL), "--verify"], capture_output=True, text=True,
                          timeout=120, env=e)


def stub_env(tmp_path, **extra):
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    ssh = bindir / "ssh"
    ssh.write_text(STUB_SSH)
    ssh.chmod(0o755)
    home = tmp_path / "stubhome"
    home.mkdir()
    return dict(PATH=f"{bindir}:{os.environ['PATH']}", STUB_HOME=str(home), **extra)


def test_handler_precedes_any_install_step():
    text = INSTALL.read_text()
    assert '[ "${1:-}" = --verify ]' in text          # the marker ops-deploy-drift looks for
    assert text.index("--verify ]") < text.index("install -m")
    assert "systemctl --user enable" in text          # the install step it must come before


def test_the_handler_lives_in_a_file_ops_deploy_drift_will_actually_run():
    """ops-deploy-drift opens `<bundle>/install.sh` by that exact name: a handler in
    install-chat-digest-local.sh is never run, so the rename is part of the fix."""
    assert INSTALL.name == "install.sh"


def test_unreachable_node_is_err_not_miss():
    r = verify(node="wq3355-no-such-host.invalid")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "ERR unreachable" in r.stdout and "VERIFY ERROR" in r.stdout
    assert "MISS" not in r.stdout


def test_each_missing_payload_file_is_one_miss(tmp_path):
    r = verify(stub_env(tmp_path))
    assert r.returncode == 1, r.stdout + r.stderr
    misses = [l for l in r.stdout.splitlines() if l.startswith("MISS")]
    assert len(misses) == 4, r.stdout
    assert any("bin/chat-digest-local" in m for m in misses)
    assert any("systemd/user/chat-digest-pull.timer" in m for m in misses)
    assert all("chat-digest-pull.timer not" not in m for m in misses)   # timer itself was OK


def test_user_manager_without_a_bus_is_err_not_miss(tmp_path):
    """`systemctl --user` over a non-interactive ssh fails to find its bus unless
    XDG_RUNTIME_DIR is set; that answer must never be reported as a missing timer."""
    r = verify(stub_env(tmp_path, STUB_ENABLED="Failed to connect to bus: No such file"))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "ERR" in r.stdout and "user manager unreachable" in r.stdout
    assert "not enabled" not in r.stdout


def test_a_disabled_timer_is_a_miss(tmp_path):
    r = verify(stub_env(tmp_path, STUB_ENABLED="disabled"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert any("chat-digest-pull.timer not enabled" in l for l in r.stdout.splitlines())


def test_verify_sets_xdg_runtime_dir_for_the_user_manager():
    assert "XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user" in (BUNDLE / ".." / "_verify.sh").read_text()
