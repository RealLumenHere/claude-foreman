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
