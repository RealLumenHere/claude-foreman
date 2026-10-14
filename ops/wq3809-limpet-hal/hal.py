#!/usr/bin/env python3
"""hal.py — LIMPET hardware abstraction layer (wq#3809).

Auto-detects RE/lab hardware connected to a node (LIMPET first, but nothing here is
LIMPET-specific) and exposes it to an agent as one JSON document: per hardware class,
whether it's plugged in, what it can do, and which device node to open.

Detection is signature-based, not probing: each `HardwarePlugin` carries a set of known
USB VID:PID pairs (from the udev rules shipped with openocd/sigrok/flashrom/bmputils and
each vendor's own docs) plus optional substring matches against the `lsusb` descriptor
string, and is checked against the live `lsusb` device list. A plugin matching is "this
kind of hardware is present"; it does not open the device or exercise it — that's left to
the tool the agent runs against the reported device node (openocd, sigrok-cli, minipro,
rtl_sdr, ...).

VID:PID is the primary signal; `desc_patterns` is a fallback for rebranded clones that
ship an unlisted PID but keep the product string. Every pattern must name a *product or
USB mode*, never a bare vendor name: wq#3814 found three plugins matching on the vendor
string alone ("realtek semiconductor", "qualcomm, inc.", "cypress semiconductor"), so an
ordinary Realtek NIC reported as an SDR receiver, a Qualcomm WWAN modem as an EDL-
flashable target, and a Cypress mouse as a logic analyzer — the exact wrong-answer failure
this module exists to prevent. wq#3819 narrowed them; `tests/test_hal.py`'s
ORDINARY_LSUSB_SAMPLE is the regression guard (probes drawn from outside the matched
population, per wq#2630).

CLI:
    python3 hal.py detect        # JSON: only hardware currently connected
    python3 hal.py list-plugins  # JSON: every supported hardware class, connected or not
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

LSUSB_LINE_RE = re.compile(
    r"^Bus\s+(?P<bus>\d+)\s+Device\s+(?P<dev>\d+):\s+ID\s+"
    r"(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})\s*(?P<desc>.*)$"
)


@dataclass(frozen=True)
class UsbDevice:
    bus: str
    dev: str
    vid: str
    pid: str
    desc: str

    @property
    def bus_path(self) -> str:
        return f"/dev/bus/usb/{self.bus}/{self.dev}"


def list_usb_devices(lsusb_output: str | None = None) -> list[UsbDevice]:
    """Parse `lsusb` output into UsbDevice rows. Pass `lsusb_output` to test without a
    real bus; otherwise this shells out to the real `lsusb`."""
    if lsusb_output is None:
        try:
            lsusb_output = subprocess.run(
                ["lsusb"], capture_output=True, text=True, check=True, timeout=10
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return []
    devices = []
    for line in lsusb_output.splitlines():
        m = LSUSB_LINE_RE.match(line.strip())
        if not m:
            continue
        devices.append(
            UsbDevice(
                bus=m["bus"],
                dev=m["dev"],
                vid=m["vid"].lower(),
                pid=m["pid"].lower(),
                desc=m["desc"].strip(),
            )
        )
    return devices


def _busnum_devnum_for_sysfs_node(device_link: str) -> tuple[str, str] | None:
    """`device_link` is a /sys/class/.../device symlink. For a composite USB device it
    resolves to the *interface* (busnum/devnum live one level up, on the device proper),
    for a simple one it resolves to the device itself — so walk up a few levels looking
    for the pair rather than assuming a fixed depth."""
    path = os.path.realpath(device_link)
    for _ in range(4):
        busnum_f = os.path.join(path, "busnum")
        devnum_f = os.path.join(path, "devnum")
        if os.path.exists(busnum_f) and os.path.exists(devnum_f):
            try:
                return (
                    open(busnum_f).read().strip(),
                    open(devnum_f).read().strip(),
                )
            except OSError:
                return None
        path = os.path.dirname(path)
    return None


def _node_for_sysfs_class(device: UsbDevice, class_globs: tuple[str, ...]) -> str | None:
    for pattern in class_globs:
        for node in sorted(glob.glob(pattern)):
            pair = _busnum_devnum_for_sysfs_node(os.path.join(node, "device"))
            if pair is None:
                continue
            busnum, devnum = pair
            if busnum.zfill(3) == device.bus and devnum.zfill(3) == device.dev:
                return f"/dev/{os.path.basename(node)}"
    return None


def _video_node_for(device: UsbDevice) -> str | None:
    """Map a USB bus/dev pair to its V4L2 capture node by following the
    /sys/class/video4linux/videoN/device symlink back to busnum/devnum — the only
    stable way to connect lsusb's bus numbering to a /dev/videoN node."""
    return _node_for_sysfs_class(device, ("/sys/class/video4linux/video*",))


def _tty_node_for(device: UsbDevice) -> str | None:
    """Same idea as _video_node_for but for /dev/ttyACM*|/dev/ttyUSB* (serial-exposed
    probes: Black Magic Probe, Bus Pirate, FTDI-based adapters)."""
    return _node_for_sysfs_class(device, ("/sys/class/tty/ttyACM*", "/sys/class/tty/ttyUSB*"))


@dataclass(frozen=True)
class HardwarePlugin:
    hw_type: str
    name: str
    capabilities: tuple[str, ...]
    usb_ids: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    #: Product- or USB-mode-identifying descriptor substrings only ("rtl2838", "qdl mode",
    #: "black magic probe"). A bare vendor name here matches every unrelated device that
    #: vendor ships — see the module docstring and wq#3814.
    desc_patterns: tuple[str, ...] = ()
    node_resolver: "callable | None" = None

    def matches(self, device: UsbDevice) -> bool:
        if (device.vid, device.pid) in self.usb_ids:
            return True
        desc_lower = device.desc.lower()
        return any(p.lower() in desc_lower for p in self.desc_patterns)

    def device_node(self, device: UsbDevice) -> str:
        if self.node_resolver is not None:
            resolved = self.node_resolver(device)
            if resolved:
                return resolved
        return device.bus_path


REGISTRY: tuple[HardwarePlugin, ...] = (
    HardwarePlugin(
        hw_type="coral_tpu",
        name="Google Coral TPU",
        capabilities=("image_classification", "on_device_ml", "edgetpu_inference"),
        usb_ids=frozenset({("1a6e", "089a"), ("18d1", "9302")}),
        # "global unichip" dropped (wq#3819): 1a6e is the vendor ID of an ASIC house whose
        # silicon is not only Coral, and both Coral USB IDs (pre- and post-firmware-upload)
        # are already in usb_ids. PCIe/M.2 Corals are not lsusb-visible at all, so they are
        # out of scope for this detector — see README "Known gaps".
        desc_patterns=("coral",),
    ),
    HardwarePlugin(
        hw_type="hdmi_capture",
        name="Magewell HDMI Capture",
        capabilities=("visual_input", "screen_reading", "kvm"),
        usb_ids=frozenset({("2935", "0008"), ("2935", "0007")}),
        desc_patterns=("magewell",),
        node_resolver=_video_node_for,
    ),
    HardwarePlugin(
        hw_type="debug_probe",
        name="USB debug probe (JTAG/SWD)",
        capabilities=("jtag", "swd", "openocd_target", "gdb_server"),
        usb_ids=frozenset(
            {
                ("1d50", "6018"),  # Black Magic Probe
                ("0483", "3748"),  # ST-Link/V2
                ("0483", "374b"),  # ST-Link/V2-1
                ("1366", "0101"),  # SEGGER J-Link
                ("0403", "6010"),  # FTDI FT2232H (Tigard and other OpenOCD-via-FTDI probes)
            }
        ),
        desc_patterns=("black magic probe", "st-link", "j-link", "tigard"),
        node_resolver=_tty_node_for,
    ),
    HardwarePlugin(
        hw_type="logic_analyzer",
        name="USB logic analyzer (sigrok)",
        capabilities=("logic_capture", "protocol_decode", "pulseview"),
        usb_ids=frozenset(
            {
                ("0925", "3881"),  # Saleae Logic (original)
                ("04b4", "8613"),  # Cypress FX2 unconfigured (fx2lafw clone analyzers)
                ("2a0e", "0001"),  # DSLogic
            }
        ),
        # "cypress semiconductor" dropped (wq#3819): it matched every Cypress-based mouse,
        # keyboard and hub. The unconfigured-FX2 clones it was meant to catch present
        # 04b4:8613, already in usb_ids above.
        desc_patterns=("saleae", "dslogic"),
    ),
    HardwarePlugin(
        hw_type="flash_programmer",
        name="Flash programmer (CH341A/TL866/flashrom)",
        capabilities=("spi_flash_read", "spi_flash_write", "eeprom_program", "minipro"),
        usb_ids=frozenset({("1a86", "5512"), ("04d8", "e11c")}),
        desc_patterns=("ch341a", "xgecu", "tl866"),
    ),
    HardwarePlugin(
        hw_type="bus_interface",
        name="Bus interface (Bus Pirate/Glasgow)",
        capabilities=("i2c", "spi", "uart", "generic_bus_bridge"),
        usb_ids=frozenset({("0403", "6001"), ("20b7", "9db1")}),
        desc_patterns=("bus pirate", "glasgow"),
        node_resolver=_tty_node_for,
    ),
    HardwarePlugin(
        hw_type="target_device",
        name="Target device (ADB/EDL/MaskROM)",
        capabilities=("adb", "edl_flash", "maskrom_flash"),
        usb_ids=frozenset(
            {
                ("05c6", "9008"),  # Qualcomm EDL (9008 mode)
                ("2207", "330c"),  # Rockchip MaskROM
                ("1f3a", "efe8"),  # Allwinner FEL
            }
        ),
        # wq#3819: vendor names dropped. "qualcomm, inc." matched every WWAN modem and
        # every Android phone in its normal mode; "rockchip"/"allwinner" matched any SoC
        # board in any mode (and "allwinner" never matched the FEL device anyway — lsusb
        # renders 1f3a:efe8 as "Onda (unverified) V972 tablet in flashing mode"). What is
        # left is the mode string lsusb prints for an EDL device, "Qualcomm, Inc. Gobi
        # Wireless Modem (QDL mode)", plus the two other bootrom-mode strings lsusb prints
        # from its own ID table: "RK3399 in Mask ROM mode" (2207:330c) and Allwinner FEL's
        # "... tablet in flashing mode" (1f3a:efe8).
        desc_patterns=("qdl mode", "mask rom mode", "in flashing mode"),
    ),
    HardwarePlugin(
        hw_type="sdr_receiver",
        name="SDR receiver (RTL-SDR)",
        capabilities=("rf_capture", "iq_sample", "rtl_sdr"),
        usb_ids=frozenset({("0bda", "2838"), ("0bda", "2832")}),
        # wq#3819: "realtek semiconductor" matched every Realtek NIC, WiFi dongle and card
        # reader on the bus. The RTL2832U/RTL2838 chip names are what lsusb prints for a
        # real dongle ("Realtek Semiconductor Corp. RTL2838 DVB-T"), rebrands included.
        desc_patterns=("rtl2832", "rtl2838"),
    ),
)


def detect(devices: list[UsbDevice] | None = None) -> list[dict]:
    if devices is None:
        devices = list_usb_devices()
    found = []
    for plugin in REGISTRY:
        for device in devices:
            if plugin.matches(device):
                found.append(
                    {
                        "hw_type": plugin.hw_type,
                        "name": plugin.name,
                        "capabilities": list(plugin.capabilities),
                        "device_node": plugin.device_node(device),
                        "status": "connected",
                        "usb_id": f"{device.vid}:{device.pid}",
                        "descriptor": device.desc,
                    }
                )
    return found


def list_plugins() -> list[dict]:
    return [
        {
            "hw_type": plugin.hw_type,
            "name": plugin.name,
            "capabilities": list(plugin.capabilities),
            "status": "not_connected",
        }
        for plugin in REGISTRY
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("detect", help="JSON of currently connected hardware")
    sub.add_parser("list-plugins", help="JSON of every supported hardware class")
    args = parser.parse_args(argv)

    if args.command == "detect":
        print(json.dumps(detect(), indent=2))
    elif args.command == "list-plugins":
        print(json.dumps(list_plugins(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
