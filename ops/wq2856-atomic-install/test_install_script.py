"""wq#2856: install-script must replace a script by rename so a running bash
keeps executing the bytes it started with.

The cp control case reproduces the lumen-re-sync failure (bash resuming the NEW
file at the OLD offset); it is what makes the install-script case able to fail.
"""
import os
import shutil
import stat
import subprocess
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "install-script")

FIRST_LINE = "sleep 0.6\n"
OLD = "#!/bin/bash\n" + FIRST_LINE + "echo OLD\n"
# Same length up to the byte where the running bash resumes, then a different
# command: an in-place overwrite makes the old process run it.
NEW = "#!/bin/bash\n" + "#" * (len(FIRST_LINE) - 1) + "\n" + "echo HIJACKED\n"


def _write(path, body, mode=0o755):
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, mode)


def _race(tmp_path, replace):
    dst = tmp_path / "bin" / "job"
    dst.parent.mkdir()
    _write(dst, OLD)
    src = tmp_path / "job.new"
    _write(src, NEW, 0o644)
    proc = subprocess.Popen(["bash", str(dst)], stdout=subprocess.PIPE, text=True)
    time.sleep(0.25)  # bash is now inside `sleep`, holding an offset into dst
    replace(str(src), str(dst))
    out, _ = proc.communicate(timeout=10)
    return out.strip(), dst


def test_cp_in_place_hijacks_running_script(tmp_path):
    out, _ = _race(tmp_path, shutil.copyfile)
    assert out == "HIJACKED"


def test_install_script_lets_running_script_finish_old_bytes(tmp_path):
    def replace(src, dst):
        subprocess.run([TOOL, src, dst], check=True, capture_output=True)

    out, dst = _race(tmp_path, replace)
    assert out == "OLD"
    assert dst.read_text() == NEW
    # MODE defaults to the replaced file's mode, not the source's 0644.
    assert stat.S_IMODE(dst.stat().st_mode) == 0o755
    assert [p.name for p in dst.parent.iterdir()] == ["job"]  # no temp left behind


def test_git_ff_merge_lets_running_script_finish_old_bytes(tmp_path):
    # lumen-pr land deploys /home/lumen/bin by `git merge --ff-only`; that path is
    # only exempt from install-script because git writes a changed file as a new
    # inode. (Compare behaviour, not st_ino: once nothing holds the old file its
    # inode number is free to be handed straight back to the new one.)
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*a):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                        "-c", "user.email=t@t", *a], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    _write(repo / "job", OLD)
    git("add", "job")
    git("commit", "-qm", "old")
    git("checkout", "-qb", "next")
    _write(repo / "job", NEW)
    git("commit", "-qam", "new")
    git("checkout", "-q", "main")
    assert (repo / "job").read_text() == OLD

    proc = subprocess.Popen(["bash", str(repo / "job")], stdout=subprocess.PIPE, text=True)
    time.sleep(0.25)
    git("merge", "-q", "--ff-only", "next")
    out, _ = proc.communicate(timeout=10)
    assert (repo / "job").read_text() == NEW
    assert out.strip() == "OLD"


def run(*args):
    return subprocess.run([TOOL, *args], capture_output=True, text=True)


def test_new_inode_and_directory_destination(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write(bindir / "tool", "old\n")
    before = (bindir / "tool").stat().st_ino
    src = tmp_path / "tool"
    _write(src, "new\n", 0o600)
    p = run(str(src), str(bindir))
    assert p.returncode == 0, p.stderr
    assert (bindir / "tool").read_text() == "new\n"
    assert (bindir / "tool").stat().st_ino != before


def test_fresh_install_defaults_0755_and_explicit_mode(tmp_path):
    src = tmp_path / "s"
    _write(src, "x\n", 0o600)
    assert run(str(src), str(tmp_path / "a")).returncode == 0
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o755
    assert run("-m", "0700", str(src), str(tmp_path / "b")).returncode == 0
    assert stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o700


def test_unchanged_is_a_noop(tmp_path):
    src = tmp_path / "s"
    _write(src, "x\n")
    dst = tmp_path / "d"
    _write(dst, "x\n")
    ino = dst.stat().st_ino
    p = run(str(src), str(dst))
    assert p.returncode == 0 and p.stdout.startswith("unchanged:")
    assert dst.stat().st_ino == ino


def test_symlink_destination_replaces_target(tmp_path):
    real = tmp_path / "real"
    _write(real, "old\n")
    link = tmp_path / "link"
    link.symlink_to(real)
    src = tmp_path / "s"
    _write(src, "new\n")
    assert run(str(src), str(link)).returncode == 0
    assert link.is_symlink() and real.read_text() == "new\n"


@pytest.mark.parametrize("args", [[], ["only-one"], ["-m", "u+x", "a", "b"], ["-z", "a", "b"]])
def test_bad_usage_refused(tmp_path, args):
    assert run(*args).returncode in (1, 2)


def test_missing_source_and_same_file_refused(tmp_path):
    f = tmp_path / "f"
    _write(f, "x\n")
    assert run(str(tmp_path / "nope"), str(tmp_path / "d")).returncode == 1
    assert run(str(f), str(f)).returncode == 1
