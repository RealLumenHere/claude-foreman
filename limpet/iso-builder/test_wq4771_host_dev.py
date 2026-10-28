"""wq#4771: stage1b bind-mounted the host's /dev and /dev/pts into the chroot. Under
systemd's shared propagation that made $CHROOT/dev a peer of the host /dev, so the
teardown's umount of $CHROOT/dev/pts reached the host (Lumen ran 3 days with no
/dev/pts: every ssh -t failed), and a chown through $CHROOT/dev hit the host's real
/dev/ptmx (found lumen:lumen).

Each test here fails against the pre-wq#4771 build.sh (wq#2630): the old stage1b logs
`--bind /dev`, the old _unmount_retry unmounts /dev/pts when asked, the old `all` never
calls unshare, and the old WORKDIR accepted /dev.
"""
import os
import subprocess

from test_layered_remaster import BUILD, FAKE_FINDMNT, FAKE_ID, FAKE_MOUNT, FAKE_MOUNTPOINT, FAKE_UMOUNT, _exe, _stage1_env


def _calls(tmp_path):
    return (tmp_path / "mounts.args").read_text().splitlines()


def test_stage1b_builds_its_own_dev_and_never_binds_the_hosts(tmp_path):
    env, work = _stage1_env(tmp_path, fake_id=True)
    r = subprocess.run(["bash", str(BUILD), "stage1b_assemble_chroot"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    chroot = os.path.join(os.path.realpath(work), "chroot")  # the fake overlay is a symlink: don't follow it
    calls = _calls(tmp_path)
    assert not [c for c in calls if "--bind" in c or "--rbind" in c], calls
    assert not [c for c in calls if " /dev " in f" {c} " or " /dev/pts " in f" {c} "], calls

    mounts = [c for c in calls if not c.startswith("-m ")]
    private = mounts.index(f"--make-private {chroot}")
    overlay = next(i for i, c in enumerate(mounts) if c.startswith("-t overlay"))
    assert overlay < private, "the chroot must be made private right after the overlay mount"
    submounts = [i for i, c in enumerate(mounts) if c.split()[-1].startswith(f"{chroot}/")]
    assert submounts and min(submounts) > private, "a chroot submount was made before --make-private"

    assert f"-t tmpfs -o mode=0755,nosuid tmpfs {chroot}/dev" in mounts
    devpts = [c for c in mounts if c.startswith("-t devpts")]
    assert devpts == [f"-t devpts -o newinstance,ptmxmode=0666,mode=0620,gid=5,nosuid,noexec devpts {chroot}/dev/pts"]
    nodes = [c for c in calls if c.startswith("-m ")]
    assert nodes and all(c.split()[2].startswith(f"{chroot}/dev/") for c in nodes), nodes
    assert os.readlink(work / "chroot/dev/ptmx") == "pts/ptmx"


def _guard_env(tmp_path, mounted):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "mount", FAKE_MOUNT)
    _exe(bin_dir / "umount", '#!/usr/bin/env bash\necho "$*" >>"$FAKE_MNT.umounts"\n' + FAKE_UMOUNT.split("\n", 1)[1])
    _exe(bin_dir / "mountpoint", FAKE_MOUNTPOINT)
    _exe(bin_dir / "findmnt", FAKE_FINDMNT)
    _exe(bin_dir / "id", FAKE_ID)
    work = tmp_path / "work"
    (work / "chroot").mkdir(parents=True)
    fake_mnt = tmp_path / "mounts"
    fake_mnt.write_text("".join(f"{m}\n" for m in mounted))
    return {"PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "FAKE_MNT": str(fake_mnt),
            "LIMPET_ISO_WORKDIR": str(work)}, work, fake_mnt


def test_unmount_refuses_paths_outside_workdir(tmp_path):
    # /dev/pts is the host mount the 2026-10-25 teardown took out; work-evil shares
    # WORKDIR's string prefix without being under it.
    work = os.path.realpath(tmp_path / "work")
    outside = ["/dev/pts", f"{work}-evil/chroot", work]
    env, _, fake_mnt = _guard_env(tmp_path, outside + [f"{work}/chroot"])
    script = 'source "$1"; shift; for m in "$@"; do _unmount_retry "$m"; done'
    r = subprocess.run(["bash", "-c", script, "_", str(BUILD), *outside, f"{work}/chroot"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    umounts = (tmp_path / "mounts.umounts").read_text().splitlines()
    assert umounts == [f"{work}/chroot"]
    assert fake_mnt.read_text().splitlines() == outside
    for m in outside:
        assert f"refusing to unmount {m} -- not under {work}" in r.stderr


def test_workdir_on_a_host_system_path_is_refused(tmp_path):
    for bad in ("/dev", "/dev/pts", "/", "/run/limpet", "/tmp/../proc"):
        r = subprocess.run(["bash", str(BUILD), "teardown"], env={"PATH": "/usr/bin:/bin", "LIMPET_ISO_WORKDIR": bad},
                           capture_output=True, text=True)
        assert r.returncode == 1, (bad, r.stderr)
        assert "is a host system path" in r.stderr, (bad, r.stderr)


def test_relative_workdir_is_canonicalised(tmp_path):
    env, work, _ = _guard_env(tmp_path, [])
    env["LIMPET_ISO_WORKDIR"] = "work/../work"
    r = subprocess.run(["bash", "-c", 'source "$1"; echo "$WORKDIR"', "_", str(BUILD)],
                       env=env, cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == os.path.realpath(work)


def _unshare_env(tmp_path, fake_id=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "unshare", '#!/usr/bin/env bash\necho "LIMPET_ISO_MOUNT_NS=$LIMPET_ISO_MOUNT_NS $*" >"$UNSHARE_LOG"\n')
    if fake_id:
        _exe(bin_dir / "id", FAKE_ID)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "UNSHARE_LOG": str(tmp_path / "unshare.log"),
            "LIMPET_ISO_WORKDIR": str(tmp_path / "work")}


def test_all_reexecs_itself_in_a_private_mount_namespace(tmp_path):
    env = _unshare_env(tmp_path)
    r = subprocess.run(["bash", str(BUILD), "all"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "unshare.log").read_text().strip() == (
        f"LIMPET_ISO_MOUNT_NS=1 --mount --propagation private -- bash {BUILD.resolve()} all")
    assert "stage1:" not in r.stderr, "a stage ran in the host mount namespace before the re-exec"


def test_all_refuses_non_root(tmp_path):
    assert os.geteuid() != 0, "this test assumes the test runner itself is not root"
    env = _unshare_env(tmp_path, fake_id=False)
    r = subprocess.run(["bash", str(BUILD), "all"], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "'all' must run as root" in r.stderr
    assert not (tmp_path / "unshare.log").exists()


def test_all_stops_if_unshare_did_not_isolate(tmp_path):
    # Re-executed (LIMPET_ISO_MOUNT_NS=1) yet still unable to prove a namespace distinct
    # from PID 1's: fail rather than re-exec forever or build in the host namespace.
    env = _unshare_env(tmp_path)
    env["LIMPET_ISO_MOUNT_NS"] = "1"
    r = subprocess.run(["bash", str(BUILD), "all"], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "still not in a private mount namespace" in r.stderr
    assert not (tmp_path / "unshare.log").exists()
