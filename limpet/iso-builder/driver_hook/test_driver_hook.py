"""wq#3980 tests: all run without root/network (fake chroot = tmp dir)."""
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import driver_hook as dh  # noqa: E402

PIN = dh.read_pin()
GOOD_COMMIT = "d" * 40


def _dev(**kw):
    base = {"name": "X", "linux_driver": "fakemod (out-of-tree, DKMS)", "dkms": True,
            "dkms_source": "https://example.invalid/x", "firmware_blob": False}
    base.update(kw)
    return base


def _bundle(tmp_path):
    b = tmp_path / "bundle"
    dh.bid.write_bundle(dh.bid.load_catalog(), b)
    return b


def test_pin_is_sane():
    assert PIN["KERNEL_ABI"] == "7.0.0-14-generic"
    assert len(PIN["BASE_ISO_SHA256"]) == 64


def test_plan_real_catalog():
    pins, skips = dh.read_sources()
    plan = dh.build_plan(dh.bid.load_catalog(), PIN, pins, skips)
    assert all(d["status"] == "skipped" for d in plan["dkms"])
    assert "xusbdfwu.hex" in plan["firmware"]["proprietary"]
    assert "xusbdfwu.hex" not in plan["firmware"]["apt"]
    assert "linux-firmware" in plan["firmware"]["apt"]
    assert all(not n.startswith(("bundled:", "proprietary:")) for n in plan["firmware"]["apt"])


def test_unpinned_dkms_fails_closed():
    with pytest.raises(dh.HookError) as e:
        dh.build_plan([_dev()], PIN, {}, {})
    assert e.value.code == dh.E_SOURCE


def test_branch_not_commit_rejected(tmp_path):
    p = tmp_path / "s.pin"
    p.write_text("pin fakemod https://example.invalid/x main\n")
    with pytest.raises(dh.HookError) as e:
        dh.read_sources(p)
    assert e.value.code == dh.E_SOURCE


def test_bad_apt_name_rejected():
    with pytest.raises(dh.HookError):
        dh.classify_firmware("linux-firmware; rm -rf /")


def test_pinned_dkms_script_is_hash_checked_and_valid_bash(tmp_path):
    plan = dh.build_plan([_dev()], PIN, {"fakemod": ("https://example.invalid/x", GOOD_COMMIT)}, {})
    s = dh.chroot_script(plan)
    assert f"checkout --detach {GOOD_COMMIT}" in s
    assert f'rev-parse HEAD)" = {GOOD_COMMIT}' in s
    assert "linux-headers-7.0.0-14-generic" in s and "-k 7.0.0-14-generic" in s
    f = tmp_path / "s.sh"; f.write_text(s)
    assert subprocess.run(["bash", "-n", str(f)]).returncode == 0


def test_install_into_fake_chroot_and_verify(tmp_path):
    chroot = tmp_path / "chroot"; chroot.mkdir()
    plan = dh.install(chroot, _bundle(tmp_path))
    assert (chroot / "etc/udev/rules.d/90-limpet-hardware.rules").is_file()
    assert list((chroot / "etc/limpet/hardware.d").glob("*.toml"))
    assert (chroot / "etc/modprobe.d/90-limpet-hardware-modprobe.conf").is_file()
    assert (chroot / dh.STAGE / "driver-plan.json").is_file()
    s = (chroot / dh.STAGE / "install-drivers.sh").read_text()
    assert "linux-firmware" in s and "xusbdfwu" not in s
    assert dh.verify_installed(chroot)["ok"] is True
    assert plan["kernel_abi"] == PIN["KERNEL_ABI"]


def test_verify_installed_fails_on_empty_root(tmp_path):
    assert dh.verify_installed(tmp_path)["ok"] is False
    assert dh.main(["verify-installed", "--root", str(tmp_path)]) == dh.E_VERIFY


def test_chroot_kernel_mismatch_fails_closed(tmp_path):
    chroot = tmp_path / "chroot"
    (chroot / "usr/lib/modules/7.0.0-38-generic").mkdir(parents=True)
    with pytest.raises(dh.HookError) as e:
        dh.install(chroot, _bundle(tmp_path))
    assert e.value.code == dh.E_KERNEL


def test_chroot_kernel_match_ok(tmp_path):
    chroot = tmp_path / "chroot"
    (chroot / "usr/lib/modules/7.0.0-14-generic").mkdir(parents=True)
    dh.install(chroot, _bundle(tmp_path))
