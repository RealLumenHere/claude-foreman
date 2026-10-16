"""wq#4075: build.sh:79's single `chroot ... apt-get purge -y $pkgs` invocation
meant one package absent from the Xubuntu 26.04 base made apt-get exit
non-zero for the WHOLE list and purge NOTHING, while the trailing
`|| log "... non-fatal"` swallowed that exit code and misreported total
failure as a benign partial.

This drives the real stage2_remove_bloat function against bloat-removal.list
(a committed fixture, pinned by the worktree -- not a live mutable file) with
a fake chroot/apt-get pair that treats one real entry from that list as
absent from the base. Falsifiable (wq#2630): if stage2 ever regresses to the
single-invocation form, every present package's purge gets skipped along with
the absent one, and test_present_packages_are_still_purged fails.
"""
import os
import stat
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"
BLOAT = HERE / "bloat-removal.list"

ABSENT_PKG = "thunderbird"
PRESENT_PKG = "libreoffice-core"


def _write_exec(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run_stage2(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "absent.list").write_text(ABSENT_PKG + "\n")

    # Fake chroot: real chroot(8) needs root and a real root fs; this stage
    # only ever uses it as "chroot <dir> <cmd...>", so dropping the dir arg
    # and exec'ing the rest reproduces that call shape without either.
    _write_exec(bin_dir / "chroot", "#!/usr/bin/env bash\nshift\nexec \"$@\"\n")

    # Fake apt-get: mirrors real apt-get purge's actual failure mode -- if
    # ANY package named in one invocation is unknown, the whole invocation
    # exits non-zero and purges NONE of the packages named in it, not just
    # the unknown one. (Checking only the first named package here would let
    # a since-fixed single-invocation bug slip the first arg through and
    # falsely pass this test against the old code.)
    _write_exec(
        bin_dir / "apt-get",
        "#!/usr/bin/env bash\n"
        'state="$FAKE_APT_STATE"\n'
        'if [ "$1" = autoremove ]; then echo autoremove >>"$state/autoremove.log"; exit 0; fi\n'
        "shift 2\n"
        "missing=0\n"
        'for pkg in "$@"; do\n'
        '  if grep -qxF "$pkg" "$state/absent.list"; then\n'
        '    echo "E: Unable to locate package $pkg" >&2\n'
        "    missing=1\n"
        "  fi\n"
        "done\n"
        '[ "$missing" = 0 ] || exit 100\n'
        'for pkg in "$@"; do echo "$pkg" >>"$state/purged.log"; done\n'
        "exit 0\n",
    )

    workdir = tmp_path / "work"
    chroot_dir = workdir / "chroot"
    chroot_dir.mkdir(parents=True)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "FAKE_APT_STATE": str(state),
        "LIMPET_ISO_WORKDIR": str(workdir),
    }
    result = subprocess.run(
        ["bash", str(BUILD), "stage2_remove_bloat"],
        env=env,
        capture_output=True,
        text=True,
    )
    purged_log = state / "purged.log"
    purged = purged_log.read_text().splitlines() if purged_log.exists() else []
    return result, purged


def test_present_packages_are_still_purged_despite_one_absent(tmp_path):
    assert ABSENT_PKG in BLOAT.read_text()
    assert PRESENT_PKG in BLOAT.read_text()
    result, purged = _run_stage2(tmp_path)
    assert result.returncode == 0, result.stderr
    assert PRESENT_PKG in purged, (
        f"{PRESENT_PKG} was not purged even though it was never reported absent "
        f"(stderr: {result.stderr})"
    )
    assert ABSENT_PKG not in purged


def test_log_does_not_misreport_a_real_failure_as_non_fatal(tmp_path):
    result, _ = _run_stage2(tmp_path)
    assert "non-fatal" not in result.stderr
    assert ABSENT_PKG in result.stderr
