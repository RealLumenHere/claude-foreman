"""wq#4527: QEMU automated testing needs SSH into the live session instead of
QMP keystroke injection (too slow/unreliable for XFCE double-clicks). But a
server with known-good auth must never reach an installed system, so
stage3b_enable_test_ssh only acts on a "*.live" layer (iso_layers.py:64
already documents minimal.standard.live as "live-session only, never
installed", and the ticket's own "live squashfs layer" wording names that
exact layer).

Falsifiable both ways (wq#2630): test_default_layer_is_skipped_not_installed
drives the stage against minimal.standard -- the layer stage3 fills because
the installer copies it to disk, i.e. an input from OUTSIDE the "live-only"
population the fix targets -- and asserts nothing gets installed. If stage3b
ever regressed to running unconditionally, that test would fail by catching
openssh-server in the install log.
"""
import stat
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"

TEST_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGVuknNYGpXz9Ht3fQ2KxwJn9r0bQe0m1b2n3c4d5e6f wq4527-test\n"


def _write_exec(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_stage3b(tmp_path, *, layer, pubkey_path=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()

    # Fake chroot: real chroot(8) needs root; this stage only ever uses it as
    # "chroot <dir> <cmd...>", so dropping the dir arg and exec'ing the rest
    # reproduces that call shape without either (mirrors test_stage2).
    _write_exec(bin_dir / "chroot", "#!/usr/bin/env bash\nshift\nexec \"$@\"\n")

    _write_exec(
        bin_dir / "apt-get",
        "#!/usr/bin/env bash\n"
        'state="$FAKE_APT_STATE"\n'
        'if [ "$1" = install ]; then\n'
        "  shift 2\n"
        '  for pkg in "$@"; do echo "$pkg" >>"$state/installed.log"; done\n'
        "fi\n"
        "exit 0\n",
    )

    _write_exec(
        bin_dir / "systemctl",
        "#!/usr/bin/env bash\n"
        'echo "$*" >>"$FAKE_APT_STATE/systemctl.log"\n'
        "exit 0\n",
    )

    workdir = tmp_path / "work"
    chroot_dir = workdir / "chroot"
    chroot_dir.mkdir(parents=True)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_APT_STATE": str(state),
        "LIMPET_ISO_WORKDIR": str(workdir),
        "LIMPET_ISO_LAYER": layer,
    }
    if pubkey_path is not None:
        env["LIMPET_TEST_SSH_PUBKEY"] = str(pubkey_path)

    result = subprocess.run(
        ["bash", str(BUILD), "stage3b_enable_test_ssh"],
        env=env,
        capture_output=True,
        text=True,
    )
    installed_log = state / "installed.log"
    installed = installed_log.read_text().splitlines() if installed_log.exists() else []
    systemctl_log = state / "systemctl.log"
    systemctl_calls = systemctl_log.read_text().splitlines() if systemctl_log.exists() else []
    return result, installed, systemctl_calls, chroot_dir


def test_live_layer_installs_ssh_and_authorizes_key(tmp_path):
    pubkey = tmp_path / "test_id_ed25519.pub"
    pubkey.write_text(TEST_PUBKEY)

    result, installed, systemctl_calls, chroot_dir = _run_stage3b(
        tmp_path, layer="minimal.standard.live", pubkey_path=pubkey
    )

    assert result.returncode == 0, result.stderr
    assert "openssh-server" in installed
    assert any("enable ssh.service" in call for call in systemctl_calls), systemctl_calls

    authorized_keys = chroot_dir / "etc" / "skel" / ".ssh" / "authorized_keys"
    assert authorized_keys.read_text() == TEST_PUBKEY
    assert stat.S_IMODE(authorized_keys.stat().st_mode) == 0o600
    assert stat.S_IMODE(authorized_keys.parent.stat().st_mode) == 0o700


def test_default_layer_is_skipped_not_installed(tmp_path):
    result, installed, systemctl_calls, chroot_dir = _run_stage3b(
        tmp_path, layer="minimal.standard"
    )

    assert result.returncode == 0, result.stderr
    assert "openssh-server" not in installed
    assert not systemctl_calls
    assert not (chroot_dir / "etc" / "skel" / ".ssh").exists()
    assert "skipping" in result.stderr


def test_live_layer_without_pubkey_fails_closed(tmp_path):
    result, installed, systemctl_calls, chroot_dir = _run_stage3b(
        tmp_path, layer="minimal.standard.live"
    )

    assert result.returncode != 0
    assert "LIMPET_TEST_SSH_PUBKEY" in result.stderr
    assert "openssh-server" not in installed
    assert not systemctl_calls
