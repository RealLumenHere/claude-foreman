"""wq#4558 option C: key-only SSH in the LIVE layer only (replaces wq#4527 stage3b).

openssh-server goes into the live layer on top of the merged dpkg database, never into
minimal.standard (the layer the installer copies to disk). Opt-in via
LIMPET_TEST_SSH_PUBKEY; unset means no SSH anywhere and no extra commands needed.

mount/chroot/apt-get/systemctl are faked (they need root and a network). The fake overlay
maps the chroot onto its upperdir, so what the script writes through it is exactly what
would land in the live layer; mounts are tracked so cleanup is checkable.
Falsifiable (wq#2630): see the PR body for the mutation checks.
"""
import stat
import subprocess
from pathlib import Path

from test_repack_live_layer import (BASE, BUILD, LIVE, MOD, _exec, _live_files, _setup, _write, stanza, status_file)

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestkeytestkeytestkeytestkeytestkeytestkey0 wq4558-test\n"

FAKE_MOUNT = r"""#!/usr/bin/env python3
import os, sys
st = os.environ["FAKE_STATE"]; a = sys.argv[1:]
open(os.environ["FAKE_LOG"], "a").write("mount " + " ".join(a) + "\n")
target = a[-1]
if os.environ.get("FAKE_MOUNT_FAIL") and "overlay" in a:
    sys.exit(32)
if "overlay" in a:
    up = [x for x in " ".join(a).replace(",", " ").split() if x.startswith("upperdir=")][0].split("=", 1)[1]
    os.rmdir(target); os.symlink(up, target)
elif "squashfs" in a:
    os.makedirs(target, exist_ok=True)
elif not os.path.isdir(target):
    sys.stderr.write("mount: %s: mount point does not exist\n" % target); sys.exit(32)
open(st + "/mounted", "a").write(target + "\n")
"""

FAKE_UMOUNT = r"""#!/usr/bin/env python3
import os, sys
st = os.environ["FAKE_STATE"]; t = sys.argv[1]
open(os.environ["FAKE_LOG"], "a").write("umount " + t + "\n")
lines = [l for l in open(st + "/mounted").read().splitlines() if l != t]
open(st + "/mounted", "w").write("".join(l + "\n" for l in lines))
"""

FAKE_MOUNTPOINT = r"""#!/usr/bin/env python3
import os, sys
t = sys.argv[-1]
lines = open(os.environ["FAKE_STATE"] + "/mounted").read().splitlines()
sys.exit(0 if t in lines else 1)
"""

FAKE_CHROOT = '#!/usr/bin/env bash\nshift\nexec "$@"\n'

FAKE_APT = r"""#!/usr/bin/env bash
echo "apt-get $*" >>"$FAKE_LOG"
[ -n "${FAKE_APT_FAIL:-}" ] && exit 100
case " $* " in
  *" install "*)
    printf '\nPackage: openssh-server\nStatus: install ok installed\nPriority: optional\nSection: net\nArchitecture: amd64\nVersion: 9.9\n' \
      >>"$LIMPET_ISO_WORKDIR/live-upper/var/lib/dpkg/status" ;;
esac
exit 0
"""

FAKE_SYSTEMCTL = r"""#!/usr/bin/env python3
import os, sys
log = open(os.environ["FAKE_LOG"], "a"); log.write("systemctl " + " ".join(sys.argv[1:]) + "\n")
root = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--root=")][0]
d = root + "/etc/systemd/system/multi-user.target.wants"
os.makedirs(d, exist_ok=True)
os.symlink("/usr/lib/systemd/system/ssh.service", d + "/ssh.service")
"""


def _prepare(tmp_path, *, ssh_pubkey="__unset__", extra_env=None, layer="minimal.standard", **setup_kw):
    bin_dir, fix, casper, changes, work = _setup(tmp_path, base=BASE, live=LIVE, modified=MOD, **setup_kw)
    for name, body in (("mount", FAKE_MOUNT), ("umount", FAKE_UMOUNT), ("mountpoint", FAKE_MOUNTPOINT),
                       ("chroot", FAKE_CHROOT), ("apt-get", FAKE_APT), ("systemctl", FAKE_SYSTEMCTL)):
        _exec(bin_dir / name, body)
    for d in ("proc", "sys", "dev/pts", "run", "usr/sbin"):          # what the real lowers provide
        _write(fix / "minimal.standard.live" / d / ".keep", "")
    state = tmp_path / "state"
    state.mkdir()
    (state / "mounted").write_text("")
    # the modified target layer's upper: must stay untouched by the SSH install
    _write(work / "layers/upper/usr/share/standard-only", "std\n")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_FIX": str(fix), "FAKE_LOG": str(tmp_path / "fake.log"),
           "FAKE_STATE": str(state), "LIMPET_ISO_WORKDIR": str(work), "BUILD": str(BUILD), "HOME": str(tmp_path)}
    if ssh_pubkey != "__unset__":
        key = tmp_path / "test_key.pub"
        key.write_text(ssh_pubkey)
        env["LIMPET_TEST_SSH_PUBKEY"] = str(key)
    env.update(extra_env or {})
    result = subprocess.run(
        ["bash", "-c", 'source "$BUILD"; repack_live_layer "$@"', "_", layer, str(changes), str(casper)],
        env=env, capture_output=True, text=True)
    log = (tmp_path / "fake.log").read_text() if (tmp_path / "fake.log").exists() else ""
    return result, changes, work, log, state


def _target_upper_files(work):
    base = work / "layers/upper"
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())


def test_default_build_has_no_ssh_and_needs_no_mounts(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "mount" not in log and "apt-get" not in log and "systemctl" not in log
    files = _live_files(changes)
    assert "openssh-server" not in files["var/lib/dpkg/status"]
    assert not any(name.startswith("etc/skel/.ssh") or "sshd_config.d" in name for name in files)


def test_default_path_works_without_systemctl_or_mount_on_the_host(tmp_path):
    # wq#4546/#4560: the no-SSH default must be a pure no-op that needs no extra commands.
    bin_dir, fix, casper, changes, work = _setup(tmp_path, base=BASE, live=LIVE, modified=MOD)
    only = tmp_path / "onlybin"
    only.mkdir()
    for tool in ("unsquashfs", "mksquashfs", "dpkg-query"):
        (only / tool).symlink_to(bin_dir / tool)
    # everything the host normally has EXCEPT the tools only the SSH path may use
    excluded = {"mount", "umount", "mountpoint", "chroot", "systemctl", "apt-get"}
    for src in sorted(Path("/usr/bin").iterdir()):
        if src.name not in excluded and not (only / src.name).exists():
            (only / src.name).symlink_to(src)
    env = {"PATH": str(only), "FAKE_FIX": str(fix), "FAKE_LOG": str(tmp_path / "fake.log"),
           "LIMPET_ISO_WORKDIR": str(work), "BUILD": str(BUILD), "HOME": str(tmp_path)}
    result = subprocess.run(["bash", "-c", 'source "$BUILD"; repack_live_layer "$@"', "_", "minimal.standard",
                             str(changes), str(casper)], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert (changes / "casper/minimal.standard.live.squashfs").exists()


def test_test_ssh_installs_sshd_into_the_live_layer_key_only(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY)

    assert result.returncode == 0, result.stderr
    files = _live_files(changes)
    status = files["var/lib/dpkg/status"]
    assert "Package: openssh-server" in status
    assert "Package: tmux" in status and "Package: cups" not in status       # merge from phase 1 intact
    dropin = files["etc/ssh/sshd_config.d/10-limpet-test.conf"]
    assert "PasswordAuthentication no" in dropin and "PermitRootLogin no" in dropin
    assert "KbdInteractiveAuthentication no" in dropin and "PubkeyAuthentication yes" in dropin
    assert files["etc/skel/.ssh/authorized_keys"] == PUBKEY
    assert "systemctl --root=" in log and "enable ssh.service" in log
    full = (changes / "casper/minimal.standard.live.manifest.full").read_text()
    assert "openssh-server\t9.9" in full                                      # manifest lists what was installed


def test_key_file_permissions_are_private(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY)

    assert result.returncode == 0, result.stderr
    # inspect the live tree as packed (tar keeps modes)
    import tarfile
    with tarfile.open(changes / "casper/minimal.standard.live.squashfs") as tar:
        modes = {m.name.lstrip("./"): stat.S_IMODE(m.mode) for m in tar.getmembers()}
    assert modes["etc/skel/.ssh/authorized_keys"] == 0o600
    assert modes["etc/skel/.ssh"] == 0o700


def test_apt_lists_and_cache_stay_out_of_the_layer(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY)

    assert result.returncode == 0, result.stderr
    assert log.count("Dir::State::lists=/run/apt-lists") == 2 and log.count("Dir::Cache=/run/apt-cache") == 2
    assert "update -qq" in log and "install -y openssh-server" in log
    assert not any(n.startswith(("var/lib/apt/lists", "var/cache/apt")) for n in _live_files(changes))


def test_the_chroot_is_the_modified_target_layer_plus_parents_over_the_live_tree(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY)

    assert result.returncode == 0, result.stderr
    layers = work / "layers"
    assert f"lowerdir={layers}/upper:{layers}/lower/minimal,upperdir={work}/live-upper," in log
    assert f"mount -t squashfs -o loop,ro {tmp_path}/casper/minimal.squashfs {layers}/lower/minimal" in log


def test_minimal_standard_never_receives_anything(tmp_path):
    before = ["usr/share/standard-only"]
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY)

    assert result.returncode == 0, result.stderr
    assert _target_upper_files(work) == before
    assert not (changes / "casper/minimal.standard.squashfs").exists()      # the target layer is not repacked here


def test_everything_is_unmounted_and_the_build_guard_removed(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY)

    assert result.returncode == 0, result.stderr
    assert (state / "mounted").read_text() == ""
    assert "usr/sbin/policy-rc.d" not in _live_files(changes)


def test_missing_or_empty_key_fails_closed(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey="")

    assert result.returncode != 0
    assert "missing or empty" in result.stderr
    assert "mount" not in log
    assert not (changes / "casper/minimal.standard.live.squashfs").exists()


def test_a_private_key_is_refused(tmp_path):
    # assembled at runtime so no key-shaped literal sits in the repo (the push secret-scan flags those)
    marker = "PRIVATE " + "KEY"
    fake = f"-----BEGIN OPENSSH {marker}-----\nnot-a-real-key\n-----END OPENSSH {marker}-----\n"
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=fake)

    assert result.returncode != 0
    assert "PRIVATE key" in result.stderr
    assert "mount" not in log


def test_the_key_text_is_never_logged(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY)

    assert "AAAAC3NzaC1lZDI1NTE5" not in result.stderr + result.stdout


def test_install_failure_still_cleans_up_and_ships_nothing(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY, extra_env={"FAKE_APT_FAIL": "1"})

    assert result.returncode != 0
    assert "not shipping a half-configured live layer" in result.stderr
    assert (state / "mounted").read_text() == ""
    assert not (changes / "casper/minimal.standard.live.squashfs").exists()


def test_a_failed_mount_stops_the_sequence_and_cleans_up(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY, extra_env={"FAKE_MOUNT_FAIL": "1"})

    assert result.returncode != 0
    assert "apt-get" not in log                                  # nothing ran after the failed overlay mount
    assert (state / "mounted").read_text() == ""
    assert not (changes / "casper/minimal.standard.live.squashfs").exists()


def test_requesting_test_ssh_when_the_target_is_the_live_layer_fails_instead_of_skipping(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY, layer="minimal.standard.live")

    assert result.returncode != 0
    assert "itself the live layer" in result.stderr
    assert "mount" not in log


def test_requesting_test_ssh_without_a_live_layer_in_the_base_iso_fails_instead_of_skipping(tmp_path):
    result, changes, work, log, state = _prepare(tmp_path, ssh_pubkey=PUBKEY, with_live_squashfs=False)

    assert result.returncode != 0
    assert "no minimal.standard.live.squashfs" in result.stderr or "has no minimal.standard.live.squashfs" in result.stderr
    assert "mount" not in log
