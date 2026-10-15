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
    modules = {t.module for t in targets}
    assert any("rtl8812bu" in m for m in modules)
    # an in-kernel, no-DKMS device must never show up as a build target
    assert not any(t.device.startswith("ASIX") for t in targets)


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
