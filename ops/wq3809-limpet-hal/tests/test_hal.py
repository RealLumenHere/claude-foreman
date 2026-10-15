"""Tests for ops/wq3809-limpet-hal/hal.py (wq#3809).

No physical hardware is required: list_usb_devices() is fed canned `lsusb` text and the
sysfs walk is exercised directly against a tmp_path tree shaped like /sys/class/*.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hal  # noqa: E402

LSUSB_SAMPLE = """\
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 002 Device 004: ID 1a86:5512 QinHeng Electronics CH341A
Bus 002 Device 005: ID 1a6e:089a Global Unichip Corp.
Bus 002 Device 006: ID 2935:0008 Magewell Electronics Co., Ltd.
Bus 002 Device 007: ID 1d50:6018 OpenMoko, Inc. Black Magic Probe
Bus 002 Device 008: ID 0bda:2838 Realtek Semiconductor Corp. RTL2838 DVB-T
Bus 002 Device 009: ID 9999:9999 Unrelated Vendor Keyboard
"""


def test_list_usb_devices_parses_lsusb_lines():
    devices = hal.list_usb_devices(LSUSB_SAMPLE)
    assert len(devices) == 7
    ch341a = devices[1]
    assert ch341a.vid == "1a86" and ch341a.pid == "5512"
    assert ch341a.bus == "002" and ch341a.dev == "004"
    assert ch341a.bus_path == "/dev/bus/usb/002/004"


def test_detect_matches_by_usb_id():
    devices = hal.list_usb_devices(LSUSB_SAMPLE)
    found = hal.detect(devices)
    hw_types = {entry["hw_type"] for entry in found}
    assert hw_types == {
        "flash_programmer",
        "coral_tpu",
        "hdmi_capture",
        "debug_probe",
        "sdr_receiver",
    }
    programmer = next(e for e in found if e["hw_type"] == "flash_programmer")
    assert programmer["device_node"] == "/dev/bus/usb/002/004"
    assert "spi_flash_write" in programmer["capabilities"]


def test_detect_ignores_unmatched_device():
    devices = hal.list_usb_devices(LSUSB_SAMPLE)
    found = hal.detect(devices)
    assert all(e["usb_id"] != "9999:9999" for e in found)


def test_detect_matches_by_descriptor_substring():
    sample = "Bus 003 Device 002: ID ffff:0001 Some Vendor Tigard\n"
    devices = hal.list_usb_devices(sample)
    found = hal.detect(devices)
    assert len(found) == 1
    assert found[0]["hw_type"] == "debug_probe"


# wq#3814/wq#3819: every line below is ordinary consumer hardware from a vendor that also
# makes RE/lab gear, with a PID that is NOT in any plugin's usb_ids — i.e. probes drawn
# from outside the population desc_patterns was written to match (wq#2630). Each one was
# a confirmed false positive before wq#3819 narrowed the vendor-name patterns: Realtek NIC
# → sdr_receiver (rf_capture/rtl_sdr), Qualcomm modem and Rockchip/Allwinner boards in
# normal mode → target_device (edl_flash/maskrom_flash), Cypress mouse → logic_analyzer.
ORDINARY_LSUSB_SAMPLE = """\
Bus 001 Device 002: ID 0bda:8153 Realtek Semiconductor Corp. RTL8153 Gigabit Ethernet Adapter
Bus 001 Device 003: ID 0bda:b812 Realtek Semiconductor Corp. RTL88x2bu 802.11a/b/g/n/ac WLAN Adapter
Bus 001 Device 004: ID 0bda:0316 Realtek Semiconductor Corp. USB3.0-CRW card reader
Bus 001 Device 005: ID 05c6:a40c Qualcomm, Inc. Mobile Broadband Modem
Bus 001 Device 006: ID 05c6:6765 Qualcomm, Inc. Android phone (MTP mode)
Bus 001 Device 007: ID 04b4:0060 Cypress Semiconductor Corp. Wireless optical mouse
Bus 001 Device 008: ID 2207:0010 Fuzhou Rockchip Electronics Company RK3288 Android device
Bus 001 Device 009: ID 1f3a:1000 Allwinner Technology A10 tablet
Bus 001 Device 010: ID 1a6e:0001 Global Unichip Corp. unrelated ASIC
"""


def test_ordinary_vendor_hardware_is_not_reported_as_re_hardware():
    devices = hal.list_usb_devices(ORDINARY_LSUSB_SAMPLE)
    assert len(devices) == 9
    assert hal.detect(devices) == []


def test_realtek_nic_is_not_an_sdr_receiver():
    sample = (
        "Bus 001 Device 003: ID 0bda:8153 Realtek Semiconductor Corp. "
        "RTL8153 Gigabit Ethernet Adapter\n"
    )
    assert hal.detect(hal.list_usb_devices(sample)) == []


def test_qualcomm_modem_is_not_an_edl_flashable_target():
    sample = "Bus 001 Device 004: ID 05c6:a40c Qualcomm, Inc. Mobile Broadband Modem\n"
    assert hal.detect(hal.list_usb_devices(sample)) == []


def test_bootrom_mode_descriptors_still_match_target_device():
    """The narrowed patterns must keep catching rebrands/unlisted PIDs in a flashing mode
    — the whole point of the desc_patterns fallback."""
    sample = (
        "Bus 001 Device 011: ID 05c6:9009 Qualcomm, Inc. Gobi Wireless Modem (QDL mode)\n"
        "Bus 001 Device 012: ID 2207:330e Fuzhou Rockchip Electronics Company "
        "RK3328 in Mask ROM mode\n"
        "Bus 001 Device 013: ID 1f3a:efe9 Onda (unverified) V972 tablet in flashing mode\n"
    )
    found = hal.detect(hal.list_usb_devices(sample))
    assert [e["hw_type"] for e in found] == ["target_device"] * 3


def test_target_device_is_enriched_from_wq3940_catalog():
    """wq#3940: a target_device match gets soc_family/mode_name/recommended_tool
    from limpet/target-devices.d/*.toml, not just the plugin's generic name."""
    sample = "Bus 001 Device 015: ID 05c6:9008 Qualcomm, Inc. QDLoader 9008\n"
    found = hal.detect(hal.list_usb_devices(sample))
    assert len(found) == 1
    matches = found[0]["target_db_matches"]
    assert matches, "expected at least one target_devices.d hit for 05c6:9008"
    assert matches[0]["vendor"] == "Qualcomm, Inc."
    assert matches[0]["recommended_tool"] == "edl / qdl"
    assert matches[0]["confidence"] == "high"


def test_target_device_enrichment_empty_for_pair_outside_catalog():
    # Probe drawn from outside the catalog's matched population (wq#2630): the
    # plugin's own desc_patterns fallback can match a target_device that the
    # wq#3940 VID:PID catalog has never heard of -- enrichment must degrade to
    # [] rather than raising or fabricating a hit.
    sample = "Bus 001 Device 016: ID dead:beef SomeVendor in Mask ROM mode\n"
    found = hal.detect(hal.list_usb_devices(sample))
    assert len(found) == 1
    assert found[0]["target_db_matches"] == []


def test_rtl_sdr_clone_with_unlisted_pid_still_matches_sdr_receiver():
    sample = (
        "Bus 001 Device 014: ID 1d19:1101 Dexatek Technology Ltd. "
        "DK RTL2832 DVB-T dongle\n"
    )
    found = hal.detect(hal.list_usb_devices(sample))
    assert [e["hw_type"] for e in found] == ["sdr_receiver"]


def test_detect_empty_bus_returns_nothing():
    assert hal.detect([]) == []


def test_list_plugins_reports_every_hardware_class_unconnected():
    plugins = hal.list_plugins()
    assert len(plugins) == len(hal.REGISTRY) == 8
    assert all(p["status"] == "not_connected" for p in plugins)
    hw_types = {p["hw_type"] for p in plugins}
    assert hw_types == {
        "coral_tpu",
        "hdmi_capture",
        "debug_probe",
        "logic_analyzer",
        "flash_programmer",
        "bus_interface",
        "target_device",
        "sdr_receiver",
    }


def test_video_node_resolution_walks_sysfs(tmp_path, monkeypatch):
    video_class = tmp_path / "sys/class/video4linux/video0"
    usb_device_dir = tmp_path / "sys/devices/usb2/2-1"
    interface_dir = usb_device_dir / "2-1:1.0"
    interface_dir.mkdir(parents=True)
    (usb_device_dir / "busnum").write_text("2\n")
    (usb_device_dir / "devnum").write_text("6\n")
    video_class.mkdir(parents=True)
    os.symlink(interface_dir, video_class / "device")

    monkeypatch.setattr(hal.glob, "glob", lambda pattern: (
        [str(video_class)] if "video4linux" in pattern else []
    ))

    device = hal.UsbDevice(bus="002", dev="006", vid="2935", pid="0008", desc="Magewell")
    assert hal._video_node_for(device) == "/dev/video0"


def test_device_node_falls_back_to_bus_path_without_resolver():
    device = hal.UsbDevice(bus="002", dev="004", vid="1a86", pid="5512", desc="CH341A")
    plugin = next(p for p in hal.REGISTRY if p.hw_type == "flash_programmer")
    assert plugin.device_node(device) == "/dev/bus/usb/002/004"


def _make_video_node(tmp_path, index, name, driver="uvcvideo"):
    node = tmp_path / "sys/class/video4linux" / f"video{index}"
    node.mkdir(parents=True)
    (node / "name").write_text(name + "\n")
    device_dir = tmp_path / "sys/devices/usb_fake" / f"dev{index}"
    device_dir.mkdir(parents=True)
    driver_dir = tmp_path / "sys/bus/usb/drivers" / driver
    driver_dir.mkdir(parents=True, exist_ok=True)
    os.symlink(driver_dir, device_dir / "driver")
    os.symlink(device_dir, node / "device")
    return node


def test_detect_video_devices_classifies_webcam_by_default(tmp_path):
    _make_video_node(tmp_path, 0, "Generic UVC WebCam: USB Camera")
    found = hal.detect_video_devices(str(tmp_path / "sys/class/video4linux/video*"))
    assert found == [{
        "hw_type": "webcam",
        "name": "Generic UVC WebCam: USB Camera",
        "capabilities": ["visual_input", "pcb_imaging"],
        "device_node": "/dev/video0",
        "status": "connected",
        "usb_id": "",
        "descriptor": "Generic UVC WebCam: USB Camera",
    }]


def test_detect_video_devices_classifies_hdmi_capture_by_name(tmp_path):
    _make_video_node(tmp_path, 1, "MACROSILICON USB Capture HDMI")
    found = hal.detect_video_devices(str(tmp_path / "sys/class/video4linux/video*"))
    assert found[0]["hw_type"] == "hdmi_capture"
    assert found[0]["capabilities"] == ["visual_input", "screen_reading", "kvm"]


def test_detect_video_devices_skips_known_nodes(tmp_path):
    _make_video_node(tmp_path, 0, "Magewell USB Capture HDMI Gen 2")
    found = hal.detect_video_devices(
        str(tmp_path / "sys/class/video4linux/video*"),
        known_device_nodes=frozenset({"/dev/video0"}),
    )
    assert found == []


def test_detect_video_devices_skips_non_uvc_driver(tmp_path):
    _make_video_node(tmp_path, 2, "Built-in codec", driver="some-other-driver")
    found = hal.detect_video_devices(str(tmp_path / "sys/class/video4linux/video*"))
    assert found == []


def test_detect_video_devices_empty_glob_returns_nothing(tmp_path):
    assert hal.detect_video_devices(str(tmp_path / "sys/class/video4linux/video*")) == []


def test_cli_detect_video_outputs_json(capsys):
    hal.main(["detect-video"])
    out = capsys.readouterr().out
    import json

    assert isinstance(json.loads(out), list)


def test_cli_detect_outputs_json(capsys):
    hal.main(["detect"])
    out = capsys.readouterr().out
    import json

    assert isinstance(json.loads(out), list)


def test_cli_list_plugins_outputs_json(capsys):
    hal.main(["list-plugins"])
    out = capsys.readouterr().out
    import json

    parsed = json.loads(out)
    assert len(parsed) == 8
