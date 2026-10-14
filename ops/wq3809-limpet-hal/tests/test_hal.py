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
