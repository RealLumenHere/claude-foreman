"""Tests for build_iso_drivers.py (wq#3924): the catalog -> ISO pre-bake plan
derivation must actually reflect what's in limpet/hardware.d/*.toml, not a
hand-maintained list that can drift from it.
"""
import build_iso_drivers as b


def test_catalog_loads_from_every_category_file():
    devices = b.load_catalog()
    names = {d["name"] for d in devices}
    # one device from a category with no DKMS/firmware need, one from each kind
    # of pre-bake need, pulled from different files -- proves the loader walks
    # the whole directory, not just the first file glob() happens to return.
    assert "Generic UVC-class USB webcam (any vendor)" in names
    assert "Realtek RTL8812BU (USB WiFi, 802.11ac dual-band)" in names
    assert "DSLogic (DreamSourceLab)" in names


def test_dkms_targets_only_include_dkms_true_devices():
    devices = b.load_catalog()
    targets = b.dkms_targets(devices)
    assert all(d.get("dkms") for d in devices if d["name"] in {t.device for t in targets})
    # wq#4093: RTL8812BU's catalog entry was corrected from dkms=true (dead
    # dkms_source URL) to dkms=false (in-kernel rtw88_8822bu covers 0bda:b812)
    # -- the catalog currently has no dkms=true device at all.
    assert targets == []
    # an in-kernel, no-DKMS device must never show up as a build target
    assert not any(t.device.startswith("ASIX") for t in targets)
    assert not any("RTL8812BU" in t.device for t in targets)


def test_firmware_targets_exclude_none_and_carry_package_name():
    devices = b.load_catalog()
    targets = b.firmware_targets(devices)
    assert all(t.package != "none" for t in targets)
    packages = {t.package for t in targets}
    assert "linux-firmware" in packages
    assert "sigrok-firmware-fx2lafw" in packages


def test_render_report_is_derived_not_static():
    devices = b.load_catalog()
    report = b.render_report(devices)
    dkms_count = len(b.dkms_targets(devices))
    fw_count = len(b.firmware_targets(devices))
    assert f"DKMS modules to pre-compile ({dkms_count})" in report
    assert f"Firmware/package targets to bundle ({fw_count})" in report


def test_udev_rules_includes_devices_with_a_rule_and_skips_empty_ones():
    devices = b.load_catalog()
    rules = b.udev_rules(devices)
    assert '"1a86", ATTR{idProduct}=="5512"' in rules  # CH341A
    # ASIX AX88179 is in-kernel with udev_rule = "" -- must not appear
    assert "ASIX AX88179" not in rules
    # the per-device standalone-file comment is dropped in the consolidated file
    assert "/etc/udev/rules.d/60-ch341a-programmer.rules" not in rules
    assert rules.startswith(f"# {b.UDEV_RULES_FILENAME}\n")


def test_proprietary_report_skips_fully_open_devices():
    devices = b.load_catalog()
    report = b.proprietary_report(devices)
    assert "Realtek RTL8812BU" in report
    # ASIX AX88179's note is "None. Fully open, in-kernel." -- must be omitted
    assert "ASIX AX88179" not in report


def test_write_bundle_writes_all_three_files(tmp_path):
    devices = b.load_catalog()
    paths = b.write_bundle(devices, tmp_path / "bundle")
    assert paths["udev_rules"].read_text() == b.udev_rules(devices)
    assert paths["prebake_plan"].read_text() == b.render_report(devices)
    assert paths["proprietary_notes"].read_text() == b.proprietary_report(devices)
    assert paths["udev_rules"].name == "90-limpet-hardware.rules"


# --- wq#3950: modprobe directives must not leak into the udev rules file ---
import shutil as _shutil
import subprocess as _sp
import pytest as _pytest


def test_rules_file_has_no_modprobe_directives():
    rules = b.udev_rules(b.load_catalog())
    for ln in rules.splitlines():
        assert not ln.startswith(("blacklist ", "install ", "options ", "alias ", "softdep ")), ln
        assert "/etc/modprobe.d/" not in ln


def test_rtl_sdr_blacklist_lands_in_modprobe_conf():
    conf = b.modprobe_conf(b.load_catalog())
    assert "blacklist dvb_usb_rtl28xxu" in conf


def test_modprobe_conf_header_only_without_directives():
    assert "blacklist" not in b.modprobe_conf([{"name": "x", "udev_rule": 'SUBSYSTEM=="usb"'}])


@_pytest.mark.skipif(_shutil.which("udevadm") is None, reason="udevadm not installed")
def test_generated_rules_pass_udevadm_verify(tmp_path):
    f = tmp_path / b.UDEV_RULES_FILENAME
    f.write_text(b.udev_rules(b.load_catalog()))
    r = _sp.run(["udevadm", "verify", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
