"""wq#4547: the stock Xubuntu 26.04 casper layers ship wget and iproute2 but not
curl or net-tools (checked in the base ISO manifest.full files), so
stage3c_install_base_utils installs the two missing ones.

Falsifiable both ways (wq#2630): the install test fails if either package is
dropped from the apt call; the ordering test fails if the stage is not
registered between stage3b and stage4, i.e. if `./build.sh all` would never run
it. The missing-chroot test fails if the stage ever runs apt without a chroot.
"""
import stat
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"


def _write_exec(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, args, *, make_chroot=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    # Fake chroot(8): drop the dir arg and exec the rest (mirrors test_stage3b).
    _write_exec(bin_dir / "chroot", "#!/usr/bin/env bash\nshift\nexec \"$@\"\n")
    _write_exec(
        bin_dir / "apt-get",
        "#!/usr/bin/env bash\n"
        'echo "$*" >>"$FAKE_APT_STATE/apt.log"\n'
        'if [ "$1" = install ]; then\n'
        "  shift 2\n"
        '  for pkg in "$@"; do echo "$pkg" >>"$FAKE_APT_STATE/installed.log"; done\n'
        "fi\n"
        "exit 0\n",
    )
    workdir = tmp_path / "work"
    if make_chroot:
        (workdir / "chroot").mkdir(parents=True)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_APT_STATE": str(state),
        "LIMPET_ISO_WORKDIR": str(workdir),
    }
    result = subprocess.run(["bash", str(BUILD), *args], env=env, capture_output=True, text=True)
    log = state / "installed.log"
    installed = log.read_text().splitlines() if log.exists() else []
    return result, installed


def test_installs_curl_and_net_tools(tmp_path):
    result, installed = _run(tmp_path, ["stage3c_install_base_utils"])

    assert result.returncode == 0, result.stderr
    assert "curl" in installed
    assert "net-tools" in installed


def test_does_not_reinstall_what_the_base_already_has(tmp_path):
    result, installed = _run(tmp_path, ["stage3c_install_base_utils"])

    assert result.returncode == 0, result.stderr
    # wget and iproute2 are already in every base layer manifest.
    assert "wget" not in installed
    assert "iproute2" not in installed


def test_fails_closed_without_a_chroot(tmp_path):
    result, installed = _run(tmp_path, ["stage3c_install_base_utils"], make_chroot=False)

    assert result.returncode != 0
    assert "chroot not assembled" in result.stderr
    assert installed == []


def test_stage_is_registered_between_3b_and_4(tmp_path):
    result, _ = _run(tmp_path, ["--help"])

    assert result.returncode == 0, result.stderr
    stages = [line.strip() for line in result.stdout.splitlines() if line.startswith("  stage")]
    assert "stage3c_install_base_utils" in stages
    assert stages.index("stage3b_enable_test_ssh") < stages.index("stage3c_install_base_utils")
    assert stages.index("stage3c_install_base_utils") < stages.index("stage4_package_re_tools")
