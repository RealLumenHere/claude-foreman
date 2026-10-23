"""wq#4077: build.sh never created $CHROOT, and stage11 packed one
filesystem.squashfs + ran grub-mkrescue on a tree with no boot/grub/grub.cfg.
Xubuntu 26.04 instead stacks casper layers (minimal -> minimal.standard ->
minimal.standard.live) and casper/install-sources.yaml names the one the
installer puts on disk.

Unit half: iso_layers.py against the real 26.04 install-sources.yaml,
grub.cfg and loopback.cfg (committed under fixtures/xubuntu-26.04/, copied
verbatim from xubuntu-26.04-desktop-amd64.iso sha256 ae030dbd...).

End-to-end half: builds a tiny three-layer ISO from those fixtures with the
real mksquashfs/xorriso, then runs the real stage1 -> stage1b -> stage6 ->
stage11 path. Only root-only calls are faked: mount/umount/mountpoint (the
overlay is emulated by pointing $CHROOT at upper/) and chroot. Falsifiable
(wq#2630): content written through $CHROOT must come out in
minimal.standard.squashfs and nowhere else -- picking the live layer, the
first source, or packing the whole chroot as one image all fail it.
"""
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.sh"
FIX = HERE / "fixtures" / "xubuntu-26.04"
LAYERS = ("minimal", "minimal.standard", "minimal.standard.live")


def _iso_layers(*args):
    return subprocess.run(
        ["python3", "-I", str(HERE / "iso_layers.py"), *args],
        capture_output=True, text=True,
    )


# --- unit: iso_layers.py on the real 26.04 metadata --------------------------

def test_target_layer_is_the_default_install_source():
    r = _iso_layers("target-layer", "--sources", str(FIX / "casper/install-sources.yaml"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "minimal.standard"


def test_target_layer_refuses_non_layered_source(tmp_path):
    doc = yaml.safe_load((FIX / "casper/install-sources.yaml").read_text())
    for s in doc["sources"]:
        s["type"] = "fsimage"
    p = tmp_path / "install-sources.yaml"
    p.write_text(yaml.safe_dump(doc))
    r = _iso_layers("target-layer", "--sources", str(p))
    assert r.returncode != 0
    assert "fsimage-layered" in r.stderr


def test_lowerdirs_nearest_parent_first():
    r = _iso_layers("lowerdirs", "--layer", "minimal.standard.live", "--root", "/L")
    assert r.stdout.strip() == "/L/minimal.standard:/L/minimal"
    assert _iso_layers("lowerdirs", "--layer", "minimal", "--root", "/L").returncode != 0


def test_grub_rebrand_keeps_kernel_args(tmp_path):
    for name in ("grub.cfg", "loopback.cfg"):
        cfg = tmp_path / name
        shutil.copy(FIX / "boot/grub" / name, cfg)
        cfg.chmod(0o644)
        before = cfg.read_text()
        assert _iso_layers("grub-cfg", "--file", str(cfg)).returncode == 0
        after = cfg.read_text()
        assert 'menuentry "Try or Install Limpet"' in after
        assert "Xubuntu" not in after
        # wq#4534: the one intentional exception is the "linux ... ---" lines,
        # which now gain the serial console args -- covered below.
        keep = [l for l in before.splitlines() if "menuentry" not in l and "linux" not in l]
        assert keep == [l for l in after.splitlines() if "menuentry" not in l and "linux" not in l]


def test_grub_rebrand_fails_loudly_on_unknown_layout(tmp_path):
    cfg = tmp_path / "grub.cfg"
    cfg.write_text('menuentry "Something else" {\n}\n')
    r = _iso_layers("grub-cfg", "--file", str(cfg))
    assert r.returncode != 0
    assert "Try or Install" in r.stderr


# --- wq#4534: grub-cfg also adds the serial console kernel args ------------
# wq#4526 fixed console=ttyS0 only on the chroot's /etc/default/grub (the
# installed system's grub), not the live ISO's own boot menu -- the one
# stage12's QEMU boot test actually boots -- so limpet-serial.log stayed
# empty. Falsifiable (wq#2630): each case below probes a boundary that a
# narrower fix could miss -- grub.cfg alone (loopback.cfg has an extra
# iso-scan/filename= token before "---"), the nomodeset menuentry (a second
# token between vmlinuz and "---"), and a second run (idempotency).
#
# wq#4564: console=ttyS0 alone still shipped a near-empty serial log because
# "quiet" (in the tail after "---") lowers console_loglevel fleet-wide, so
# the kernel/systemd print almost nothing on any console, ttyS0 included.
# grub-cfg now drops "quiet" and adds loglevel=7 systemd.show_status=1 in
# its place, read as the last word on console_loglevel since cmdline tokens
# parse left to right. "splash" is left alone -- Plymouth only owns the tty0
# framebuffer, so it never touched what printk sends to ttyS0, and dropping
# it would cost the wq#3948 graphical boot branding for no serial-log gain.

SERIAL_ARGS = "console=tty0 console=ttyS0,115200n8"
VERBOSE_ARGS = "loglevel=7 systemd.show_status=1"


@pytest.mark.parametrize("name", ["grub.cfg", "loopback.cfg"])
def test_grub_cfg_adds_serial_console_to_every_linux_line(tmp_path, name):
    cfg = tmp_path / name
    shutil.copy(FIX / "boot/grub" / name, cfg)
    cfg.chmod(0o644)
    assert _iso_layers("grub-cfg", "--file", str(cfg)).returncode == 0
    after = cfg.read_text()
    linux_lines = [l for l in after.splitlines() if l.lstrip().startswith("linux")]
    assert len(linux_lines) == 2, after
    for line in linux_lines:
        assert SERIAL_ARGS in line
        # Live-only args land before "---"; the verbosity args replace
        # "quiet" in the tail, "splash" (Plymouth branding) stays.
        assert line.index(SERIAL_ARGS) < line.index("---")
        tail = line.rsplit("---", 1)[1].strip()
        assert tail == f"splash {VERBOSE_ARGS}"
        assert "quiet" not in tail


def test_grub_cfg_serial_console_covers_nomodeset_line(tmp_path):
    cfg = tmp_path / "grub.cfg"
    shutil.copy(FIX / "boot/grub/grub.cfg", cfg)
    cfg.chmod(0o644)
    assert _iso_layers("grub-cfg", "--file", str(cfg)).returncode == 0
    after = cfg.read_text()
    nomodeset_line = next(l for l in after.splitlines() if "nomodeset" in l)
    assert SERIAL_ARGS in nomodeset_line
    assert nomodeset_line.index("nomodeset") < nomodeset_line.index(SERIAL_ARGS)


def test_grub_cfg_serial_console_is_idempotent(tmp_path):
    cfg = tmp_path / "grub.cfg"
    shutil.copy(FIX / "boot/grub/grub.cfg", cfg)
    cfg.chmod(0o644)
    assert _iso_layers("grub-cfg", "--file", str(cfg)).returncode == 0
    first = cfg.read_text()
    # Second run must re-brand "Limpet" -> "Limpet" (a no-op) without
    # duplicating the serial console args already in the line.
    r = _iso_layers("grub-cfg", "--file", str(cfg), "--old", "Limpet", "--new", "Limpet")
    assert r.returncode == 0, r.stderr
    second = cfg.read_text()
    assert second == first
    assert second.count("console=ttyS0") == 2  # one per menuentry, not duplicated
    assert second.count("loglevel=7") == 2  # one per menuentry, not duplicated


def test_grub_cfg_serial_console_fails_loudly_on_unknown_kernel_line(tmp_path):
    cfg = tmp_path / "grub.cfg"
    cfg.write_text('menuentry "Try or Install Xubuntu" {\n    initrd /casper/initrd\n}\n')
    r = _iso_layers("grub-cfg", "--file", str(cfg))
    assert r.returncode != 0
    assert "linux" in r.stderr


# --- end to end: real stages on a synthetic layered ISO -----------------------

FAKE_MOUNT = r"""#!/usr/bin/env bash
# Emulates the mounts stage1b makes. overlay: $CHROOT becomes a symlink to
# upperdir=, so writes through it land in the layer exactly as overlayfs would
# record them. squashfs: unsquash into the mountpoint. Others: bookkeeping.
target="${@: -1}"
echo "$target" >>"$FAKE_MNT"
case "$*" in
  *"-t overlay"*)
    upper="$(printf '%s\n' "$@" | tr ',' '\n' | sed -n 's/^upperdir=//p')"
    rmdir "$target" && ln -s "$upper" "$target" ;;
  *"-t squashfs"*)
    unsquashfs -f -d "$target" "${@: -2:1}" >/dev/null ;;
  *"-t tmpfs"*)
    mkdir -p "$target" ;;
esac
"""

FAKE_UMOUNT = r"""#!/usr/bin/env bash
grep -vxF "$1" "$FAKE_MNT" >"$FAKE_MNT.new" || true
mv "$FAKE_MNT.new" "$FAKE_MNT"
if [ -L "$1" ]; then rm "$1"; mkdir "$1"; exit 0; fi
case "$1" in */run) rm -rf "$1"/systemd ;; esac
"""

FAKE_MOUNTPOINT = '#!/usr/bin/env bash\n[ -f "$FAKE_MNT" ] && grep -qxF "$2" "$FAKE_MNT"\n'

# The fake chroot execs on the host, so `apt-get install` records its packages in the
# chroot's dpkg db (through the overlay symlink, i.e. into upper/) the way a real one would.
FAKE_APT_GET = r"""#!/usr/bin/env bash
[ "$1" = install ] || exit 0
for p in "$@"; do
  case "$p" in install|-*) ;; *)
    printf 'Package: %s\nStatus: install ok installed\nMaintainer: t\nArchitecture: amd64\nVersion: 1.0\nDescription: t\n\n' "$p" >>"$FAKE_DPKG_STATUS" ;;
  esac
done
"""

# One apt tool: stage4 resolves it through gen_tool_packages.py like the real catalog,
# without the container builds the cargo/pip/binary tools need.
TEST_CATALOG = {"tools": [
    {"name": "limpet-test-tool", "trust": "core", "install_method": "apt", "package": "limpet-test-pkg"},
]}

# wq#4456: stage1b now refuses to run unless EUID 0 (the mounts it makes are real root-only
# calls even though this fixture fakes them). This suite intentionally doesn't run as root
# (see module docstring), so fake `id` the same way mount/umount/mountpoint are faked.
FAKE_ID = '#!/usr/bin/env bash\n[ "$1" = "-u" ] && echo 0 || exec /usr/bin/id "$@"\n'


def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _stage4_env(tmp, bin_dir, work):
    _exe(bin_dir / "apt-get", FAKE_APT_GET)
    catalog = tmp / "tool-catalog.json"
    catalog.write_text(json.dumps(TEST_CATALOG))
    return {"LIMPET_TOOL_CATALOG": str(catalog),
            "FAKE_DPKG_STATUS": str(work / "chroot/var/lib/dpkg/status")}


def _status(*pkgs):
    return "".join(
        f"Package: {p}\nStatus: install ok installed\nMaintainer: t\nArchitecture: amd64\n"
        f"Version: 1.0\nDescription: t\n\n"
        for p in pkgs
    )


def _mksquashfs(src, dest):
    subprocess.run(
        ["mksquashfs", str(src), str(dest), "-noappend", "-comp", "xz", "-b", "131072",
         "-no-progress", "-quiet"],
        check=True, capture_output=True,
    )


# wq#4523: the real live layer snap-preseeds the installer
# (ubuntu-desktop-bootstrap -- Xubuntu 26.04 has no ubiquity; see
# verify_installer.py). The fixture mirrors that so stage11b's installer gate
# runs against the same layout it probes on a real ISO.
SEED_FILES = {
    "var/lib/snapd/seed/seed.yaml":
        "snaps:\n"
        "- name: ubuntu-desktop-bootstrap\n"
        "  channel: 26.04/stable/ubuntu-26.04\n"
        "  file: ubuntu-desktop-bootstrap_675.snap\n",
    "var/lib/snapd/seed/snaps/ubuntu-desktop-bootstrap_675.snap": "squash-stub\n",
}


def _make_base_iso(tmp, live_extra=None):
    root = tmp / "iso-src"
    (root / "casper").mkdir(parents=True)
    shutil.copytree(FIX / "boot", root / "boot")
    shutil.copy(FIX / "casper/install-sources.yaml", root / "casper")

    live = {"var/lib/dpkg/status": _status("base-files", "xubuntu-desktop", "casper"),
            "etc/live-only": "live\n", "usr/sbin/.keep": ""}
    live.update(SEED_FILES if live_extra is None else live_extra)
    trees = {
        "minimal": {"var/lib/dpkg/status": _status("base-files"),
                    "etc/os-release": 'VERSION_ID="26.04"\n'},
        # usr/sbin in every upper-capable layer: the fake overlay exposes
        # upper/ only, and a wrong-layer pick must reach the content asserts.
        "minimal.standard": {"var/lib/dpkg/status": _status("base-files", "xubuntu-desktop"),
                             "usr/share/standard-only": "std\n", "usr/sbin/.keep": ""},
        "minimal.standard.live": live,
    }
    for layer, files in trees.items():
        t = tmp / "trees" / layer
        for rel, body in files.items():
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / rel).write_text(body)
        _mksquashfs(t, root / "casper" / f"{layer}.squashfs")
    (root / "casper/minimal.manifest.full").write_text("base-files\t1.0\n")
    (root / "casper/minimal.standard.manifest.full").write_text("base-files\t1.0\nxubuntu-desktop\t1.0\n")

    lines = []
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = "./" + f.relative_to(root).as_posix()
        lines.append(f"{hashlib.md5(f.read_bytes()).hexdigest()}  {rel}")
    (root / "md5sum.txt").write_text("\n".join(lines) + "\n")

    iso = tmp / "base.iso"
    subprocess.run(["xorriso", "-outdev", str(iso), "-map", str(root), "/", "-commit"],
                   check=True, capture_output=True)
    return iso


def _extract(iso, path, dest):
    subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(iso), "-extract", path, str(dest)],
                   check=True, capture_output=True)
    return dest


def _squash_listing(sq):
    out = subprocess.run(["unsquashfs", "-l", str(sq)], check=True,
                         capture_output=True, text=True).stdout
    prefix = "squashfs-root/"
    return {l[len(prefix):] for l in out.splitlines() if l.startswith(prefix)}


@pytest.fixture(scope="module")
def remaster(tmp_path_factory):
    for tool in ("mksquashfs", "unsquashfs", "xorriso", "dpkg-query"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not installed")
    tmp = tmp_path_factory.mktemp("remaster")
    iso = _make_base_iso(tmp)
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "mount", FAKE_MOUNT)
    _exe(bin_dir / "umount", FAKE_UMOUNT)
    _exe(bin_dir / "mountpoint", FAKE_MOUNTPOINT)
    _exe(bin_dir / "id", FAKE_ID)
    _exe(bin_dir / "chroot", '#!/usr/bin/env bash\nshift\nexec "$@"\n')

    work = tmp / "work"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_MNT": str(tmp / "mounts"),
        "LIMPET_BASE_ISO": str(iso),
        "LIMPET_BASE_ISO_SHA256": hashlib.sha256(iso.read_bytes()).hexdigest(),
        "LIMPET_ISO_WORKDIR": str(work),
        "LIMPET_ISO_NAME": "limpet-test.iso",
        **_stage4_env(tmp, bin_dir, work),
    }

    def run(stage):
        r = subprocess.run(["bash", str(BUILD), stage], env=env, capture_output=True, text=True)
        assert r.returncode == 0, f"{stage} failed:\n{r.stderr}"
        return r

    run("stage1_fetch_base_iso")
    run("stage1b_assemble_chroot")
    run("stage1b_assemble_chroot")  # idempotent: second call reuses the mount
    run("stage4_package_re_tools")  # dpkg db gains limpet-test-pkg via the fake apt-get
    run("stage6_bundle_target_device_db")
    run("stage11_build_hybrid_iso")
    run("stage11b_verify_installer")  # wq#4523: installer gate on the finished ISO

    out = work / "out/limpet-test.iso"
    got = tmp / "got"
    got.mkdir()
    return {"base": iso, "out": out, "got": got, "tmp": tmp, "work": work}


def test_stage_content_lands_in_the_install_layer_only(remaster):
    toml = sorted(p.name for p in (HERE.parent / "hardware.d").glob("*.toml"))[0]
    want = f"opt/limpet/hardware.d/{toml}"
    sq = _extract(remaster["out"], "/casper/minimal.standard.squashfs", remaster["got"] / "std.sq")
    listing = _squash_listing(sq)
    assert want in listing
    assert "usr/share/standard-only" in listing  # the layer's own content survived
    assert "usr/sbin/policy-rc.d" not in listing  # build-time guard removed
    assert not any(p.startswith("run/systemd") for p in listing)


@pytest.mark.parametrize("layer", ["minimal", "minimal.standard.live"])
def test_other_layers_are_byte_identical(remaster, layer):
    a = _extract(remaster["base"], f"/casper/{layer}.squashfs", remaster["got"] / f"base-{layer}")
    b = _extract(remaster["out"], f"/casper/{layer}.squashfs", remaster["got"] / f"out-{layer}")
    assert a.read_bytes() == b.read_bytes()


def test_iso_grub_cfg_emitted_and_rebranded(remaster):
    for name in ("grub.cfg", "loopback.cfg"):
        text = _extract(remaster["out"], f"/boot/grub/{name}", remaster["got"] / name).read_text()
        assert 'menuentry "Try or Install Limpet"' in text
        assert "linux  /casper/vmlinuz" in text


def test_layer_metadata_refreshed(remaster):
    got = remaster["got"]
    src = yaml.safe_load(_extract(remaster["out"], "/casper/install-sources.yaml", got / "is.yaml").read_text())
    stock = yaml.safe_load((FIX / "casper/install-sources.yaml").read_text())
    by_path = {s["path"]: s for s in src["sources"]}
    stock_by_path = {s["path"]: s for s in stock["sources"]}
    assert by_path["minimal.squashfs"] == stock_by_path["minimal.squashfs"]
    assert by_path["minimal.standard.squashfs"]["default"] is True
    assert by_path["minimal.standard.squashfs"]["size"] != stock_by_path["minimal.standard.squashfs"]["size"]

    delta = _extract(remaster["out"], "/casper/minimal.standard.manifest", got / "delta").read_text()
    added = {l for l in delta.splitlines() if l.startswith("+") and not l.startswith("+++")}
    assert added == {"+limpet-test-pkg\t1.0", "+xubuntu-desktop\t1.0"}

    md5 = _extract(remaster["out"], "/md5sum.txt", got / "md5sum.txt").read_text()
    sq = _extract(remaster["out"], "/casper/minimal.standard.squashfs", got / "std-md5.sq")
    assert f"{hashlib.md5(sq.read_bytes()).hexdigest()}  ./casper/minimal.standard.squashfs" in md5.splitlines()


def test_mounts_all_released(remaster):
    assert (remaster["tmp"] / "mounts").read_text().strip() == ""
    assert not (remaster["work"] / "chroot").is_symlink()


# --- wq#4456: stage1b's own root/ownership guards (previously only in the external
# build-limpet.sh wrapper) ----------------------------------------------------

def _stage1_env(tmp_path, fake_id):
    for tool in ("mksquashfs", "unsquashfs", "xorriso"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} not installed")
    iso = _make_base_iso(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "mount", FAKE_MOUNT)
    _exe(bin_dir / "umount", FAKE_UMOUNT)
    _exe(bin_dir / "mountpoint", FAKE_MOUNTPOINT)
    if fake_id:
        _exe(bin_dir / "id", FAKE_ID)
    _exe(bin_dir / "chroot", '#!/usr/bin/env bash\nshift\nexec "$@"\n')
    work = tmp_path / "work"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "FAKE_MNT": str(tmp_path / "mounts"),
        "LIMPET_BASE_ISO": str(iso),
        "LIMPET_BASE_ISO_SHA256": hashlib.sha256(iso.read_bytes()).hexdigest(),
        "LIMPET_ISO_WORKDIR": str(work),
        "LIMPET_ISO_NAME": "limpet-test.iso",
        **_stage4_env(tmp_path, bin_dir, work),
    }
    r = subprocess.run(["bash", str(BUILD), "stage1_fetch_base_iso"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"stage1 setup failed:\n{r.stderr}"
    return env, work


def test_stage1b_refuses_non_root(tmp_path):
    # No fake `id`: the real (non-root, uid 1000 in this harness) id(1) answers, so this
    # probes the guard with an input squarely outside its "root" population -- it must
    # actually be able to fail, not just pass trivially (wq#2630).
    assert os.geteuid() != 0, "this test assumes the test runner itself is not root"
    env, _work = _stage1_env(tmp_path, fake_id=False)
    r = subprocess.run(["bash", str(BUILD), "stage1b_assemble_chroot"], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "must run as root" in r.stderr


def test_stage1b_refuses_stale_non_root_upper(tmp_path):
    env, work = _stage1_env(tmp_path, fake_id=True)
    # Simulate wq#4449's root cause directly: an upper/ left by a non-root unsquashfs,
    # where /usr/bin/sudo ends up owned by the extracting (non-root) user instead of uid 0.
    stale_sudo = work / "layers" / "upper" / "usr" / "bin" / "sudo"
    stale_sudo.parent.mkdir(parents=True)
    stale_sudo.write_text("#!/bin/sh\n")
    stale_sudo.chmod(0o4755)
    r = subprocess.run(["bash", str(BUILD), "stage1b_assemble_chroot"], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert "stale non-root extraction" in r.stderr


# --- wq#4524: an ISO shipped with zero RE tools; stage4 and stage11 now fail closed ------

def _build(env, stage):
    return subprocess.run(["bash", str(BUILD), stage], env=env, capture_output=True, text=True)


def test_stage4_refuses_unmounted_chroot(tmp_path):
    # Pre-fix, stage4 with no chroot wrote the manifest, installed nothing, and exited 0.
    env, work = _stage1_env(tmp_path, fake_id=True)
    r = _build(env, "stage4_package_re_tools")
    assert r.returncode != 0
    assert "chroot not mounted" in r.stderr


def test_stage4_refuses_installs_that_miss_the_chroot(tmp_path):
    # apt reports success but writes some other dpkg db (a chroot other than the
    # overlay stage1b mounted): the packages never reach upper/.
    env, work = _stage1_env(tmp_path, fake_id=True)
    assert _build(env, "stage1b_assemble_chroot").returncode == 0
    env["FAKE_DPKG_STATUS"] = str(tmp_path / "elsewhere-status")
    r = _build(env, "stage4_package_re_tools")
    assert r.returncode != 0
    assert "missing RE tool apt package: limpet-test-pkg" in r.stderr


def test_stage11_refuses_to_pack_without_stage4(tmp_path):
    # The external wrapper's stage loop skipped stage4 and still produced an ISO.
    env, work = _stage1_env(tmp_path, fake_id=True)
    assert _build(env, "stage1b_assemble_chroot").returncode == 0
    r = _build(env, "stage11_build_hybrid_iso")
    assert r.returncode != 0
    assert "stage4_package_re_tools never ran" in r.stderr
    assert not (work / "out/limpet-test.iso").exists()


def test_stage11_refuses_upper_missing_a_stage4_tool(tmp_path):
    env, work = _stage1_env(tmp_path, fake_id=True)
    assert _build(env, "stage1b_assemble_chroot").returncode == 0
    status = work / "layers/upper/var/lib/dpkg/status"
    before = status.read_text()
    assert _build(env, "stage4_package_re_tools").returncode == 0
    status.write_text(before)  # upper/ lost the install between stage4 and stage11
    r = _build(env, "stage11_build_hybrid_iso")
    assert r.returncode != 0
    assert "missing RE tool apt package: limpet-test-pkg" in r.stderr
