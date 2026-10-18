#!/usr/bin/env python3
"""Deploy probe for `install.sh --verify` (wq#3819): the live hal.py must not report
ordinary consumer USB hardware as RE/lab hardware.

Run as `verify_no_false_positive.py <path-to-live-hal.py>`. It loads that exact file (not
the bundle copy, not whatever `import hal` happens to find) and feeds it canned `lsusb`
text for four devices whose PIDs are in no plugin's `usb_ids` — a Realtek Ethernet
adapter, a Qualcomm WWAN modem, a Cypress mouse, a Samsung phone in MTP mode. The first
three were confirmed false positives from the vendor-name `desc_patterns` wq#3814 found,
so this probe fails on the pre-wq#3819 copy: it is a check that can fail (wq#2630), not a
restatement of the fix. The Samsung line (wq#4188) is catalogued as "Odin download mode"
in limpet/target-devices.d, so it fails on a copy that gates target_device on that file.

Exit 0 and print OK when the live copy reports nothing; exit 1 and name each false
positive otherwise. Read-only: no bus access, no device is opened.
"""
from __future__ import annotations

import importlib.util
import sys

ORDINARY_LSUSB = """\
Bus 001 Device 002: ID 0bda:8153 Realtek Semiconductor Corp. RTL8153 Gigabit Ethernet Adapter
Bus 001 Device 005: ID 05c6:a40c Qualcomm, Inc. Mobile Broadband Modem
Bus 001 Device 007: ID 04b4:0060 Cypress Semiconductor Corp. Wireless optical mouse
Bus 001 Device 008: ID 04e8:6860 Samsung Electronics Co., Ltd Galaxy series, misc. (MTP mode)
"""


def load(hal_path: str):
    spec = importlib.util.spec_from_file_location("hal_under_verify", hal_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"not importable: {hal_path}")
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves its own module out of sys.modules while the class body executes,
    # so the entry has to exist before exec_module, not after it (python 3.14).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <path-to-hal.py>", file=sys.stderr)
        return 2
    hal = load(argv[1])
    devices = hal.list_usb_devices(ORDINARY_LSUSB)
    if len(devices) != 4:
        print(f"probe corpus parsed as {len(devices)} devices, expected 4")
        return 1
    found = hal.detect(devices)
    if found:
        for entry in found:
            print(
                f"false positive: {entry['usb_id']} ({entry['descriptor']}) "
                f"reported as {entry['hw_type']} with {','.join(entry['capabilities'])}"
            )
        return 1
    print("OK 4 ordinary-vendor devices reported as no RE hardware")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
