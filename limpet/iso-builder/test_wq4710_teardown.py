"""wq#4710: stage1b mounts an overlay chroot (plus proc/sysfs/devtmpfs/tmpfs) under
WORKDIR and nothing unmounted them on a killed/failed run -- a watchdog SIGTERM or a
dead fworker left them stuck, and devtmpfs inside the tree means an `rm -rf WORKDIR`
(worktree prune, a pruner timer) would descend into the host's real device nodes.

Reuses test_layered_remaster.py's fake mount/umount/mountpoint/findmnt/id harness
(bookkeeping via $FAKE_MNT) rather than duplicating it.

Falsifiable (wq#2630): test_sigterm_mid_run_tears_down_mounts actually sends SIGTERM
to a running build.sh and asserts no mount survives -- a no-op trap, or one that only
fires on a clean exit, would leave the mount recorded in $FAKE_MNT and fail this.
"""
import subprocess

from test_layered_remaster import (
    BUILD,
    FAKE_FINDMNT,
    FAKE_ID,
    FAKE_MOUNT,
    FAKE_MOUNTPOINT,
    FAKE_UMOUNT,
    _exe,
    _stage1_env,
)


def test_teardown_stage_unmounts_everything(tmp_path):
    env, work = _stage1_env(tmp_path, fake_id=True)
    r = subprocess.run(["bash", str(BUILD), "stage1b_assemble_chroot"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "mounts").read_text().strip() != ""

    r = subprocess.run(["bash", str(BUILD), "teardown"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "mounts").read_text().strip() == ""
    assert "clean" in r.stderr


def test_stage1b_refuses_orphaned_submount(tmp_path):
    # wq#4710: $CHROOT itself is NOT mounted (so the "already mounted -- reusing it"
    # path does not fire), but a submount survives under WORKDIR anyway -- exactly
    # what a run killed between mounting chroot/run and the final overlay mount, or a
    # partial release_chroot, leaves behind. This input sits outside the population
    # the pre-wq#4710 code handled (it only ever checked $CHROOT), so it is able to
    # fail against the old code path (wq#2630).
    env, work = _stage1_env(tmp_path, fake_id=True)
    chroot = work / "chroot"
    chroot.mkdir(parents=True)
    (chroot / "run").mkdir()
    (tmp_path / "mounts").write_text(f"{chroot / 'run'}\n")

    r = subprocess.run(["bash", str(BUILD), "stage1b_assemble_chroot"], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "already has mount(s) left from a previous run" in r.stderr


def test_sigterm_mid_run_tears_down_mounts(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "mount", FAKE_MOUNT)
    _exe(bin_dir / "umount", FAKE_UMOUNT)
    _exe(bin_dir / "mountpoint", FAKE_MOUNTPOINT)
    _exe(bin_dir / "findmnt", FAKE_FINDMNT)
    _exe(bin_dir / "id", FAKE_ID)
    work = tmp_path / "work"
    chroot = work / "chroot"
    chroot.mkdir(parents=True)
    fake_mnt = tmp_path / "mounts"
    fake_mnt.write_text(f"{chroot}\n")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_MNT": str(fake_mnt),
        "LIMPET_ISO_WORKDIR": str(work),
    }
    script = f'source "{BUILD}"; kill -TERM $$; sleep 5'
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    assert r.returncode == 143, r.stderr
    assert "received SIGTERM" in r.stderr
    assert fake_mnt.read_text().strip() == ""


def test_teardown_refuses_when_a_mount_wont_release(tmp_path):
    # refuse_rm_if_mounted is the belt-and-braces half of the class fix: whatever
    # removes WORKDIR must be refused, loudly, if a mount is still held open (fuser/
    # lsof territory) after teardown's own unmount attempts. A fake umount that never
    # actually clears the bookkeeping file stands in for "something still has it open".
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "mount", FAKE_MOUNT)
    _exe(bin_dir / "umount", "#!/usr/bin/env bash\nexit 1\n")
    _exe(bin_dir / "mountpoint", FAKE_MOUNTPOINT)
    _exe(bin_dir / "findmnt", FAKE_FINDMNT)
    _exe(bin_dir / "id", FAKE_ID)
    work = tmp_path / "work"
    chroot = work / "chroot"
    chroot.mkdir(parents=True)
    fake_mnt = tmp_path / "mounts"
    fake_mnt.write_text(f"{chroot}\n")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_MNT": str(fake_mnt),
        "LIMPET_ISO_WORKDIR": str(work),
    }
    r = subprocess.run(["bash", str(BUILD), "teardown"], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "refusing to remove" in r.stderr
    assert str(chroot) in r.stderr


def test_clean_single_stage_exit_keeps_mounts(tmp_path):
    # The resumable-stage design (header comment: "Each stage is independently
    # runnable") depends on a clean exit NOT tearing down -- only a non-zero exit or a
    # signal does. Otherwise `build.sh stage1b_assemble_chroot` followed by a separate
    # `build.sh stage2_remove_bloat` invocation could never see the mount stage1b left.
    env, work = _stage1_env(tmp_path, fake_id=True)
    r = subprocess.run(["bash", str(BUILD), "stage1b_assemble_chroot"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "mounts").read_text().strip() != ""
