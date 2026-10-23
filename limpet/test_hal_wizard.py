"""wq#4506 -- tests for the HAL device-event subscriber + wizard.

Falsifiable by construction: a device_removed event, a malformed line, and a
capability-less device are inputs from OUTSIDE the device_added/well-formed
population the wizard was designed around (ISSUE-LOG wq#2630), so each test can
fail if dispatch over-fires or the menu collapses. The socketpair test drives
the real `socket.recv` path, not a stand-in.
"""
import json
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hal_wizard  # noqa: E402

# A flash programmer as hal.detect() would report it (hal.py plugin row).
FLASH_DEVICE = {
    "hw_type": "flash_programmer",
    "name": "TL866II Plus / T48 universal programmer",
    "capabilities": ["spi_flash_read", "spi_flash_write", "eeprom_program", "minipro"],
    "device_node": "/dev/bus/usb/001/007",
    "status": "connected",
    "usb_id": "04d8:e11c",
    "descriptor": "Microchip Technology, Inc.",
}


def _added(device):
    return {"type": hal_wizard.EVENT_DEVICE_ADDED, "device": device, "timestamp": 1.0}


def test_wizard_lists_capability_actions_then_base_actions():
    prompt = hal_wizard.build_wizard_prompt(FLASH_DEVICE)
    assert "TL866II Plus / T48 universal programmer" in prompt
    assert "04d8:e11c" in prompt
    assert "1) Dump flash contents" in prompt
    assert "2) Program / write flash" in prompt
    # The four flash capabilities fold to two actions, so the two base actions
    # land at 3 and 4 -- not 5/6 (proves de-duplication actually happened).
    assert "3) Identify this hardware in detail" in prompt
    assert "4) Something else (describe what you want to do)" in prompt


def test_capability_less_device_still_gets_base_menu():
    device = {"name": "Mystery gadget", "usb_id": "dead:beef", "capabilities": []}
    options = hal_wizard.wizard_options(device)
    assert options == [
        "Identify this hardware in detail",
        "Something else (describe what you want to do)",
    ]


def test_missing_fields_degrade_gracefully():
    prompt = hal_wizard.build_wizard_prompt({})
    assert "Unknown device" in prompt
    assert "unknown" in prompt


def test_process_event_injects_only_on_device_added():
    sent = []
    sub = hal_wizard.HalEventSubscriber(sent.append)
    assert sub.process_event(_added(FLASH_DEVICE)) is True
    assert len(sent) == 1 and "Dump flash contents" in sent[0]
    # A removal must NOT inject.
    assert sub.process_event({"type": "device_removed", "device": FLASH_DEVICE}) is False
    # A device_added with no device payload must NOT inject.
    assert sub.process_event({"type": hal_wizard.EVENT_DEVICE_ADDED}) is False
    assert len(sent) == 1


def test_pump_skips_malformed_lines_and_counts_injections():
    sent = []
    sub = hal_wizard.HalEventSubscriber(sent.append)
    reader = [
        b"not json\n".strip(),
        json.dumps(_added(FLASH_DEVICE)).encode(),
        json.dumps({"type": "device_removed", "device": FLASH_DEVICE}).encode(),
        b"",
        json.dumps(_added({"name": "Mystery", "usb_id": "dead:beef"})).encode(),
    ]
    assert sub.pump(reader) == 2
    assert len(sent) == 2


def test_run_over_real_socketpair():
    """Exercise the live recv path: daemon side writes JSON lines, subscriber
    side injects, then the daemon closes and run() returns the count."""
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    sent = []
    sub = hal_wizard.HalEventSubscriber(sent.append)
    sub._sock = client  # pre-connected; run() will pump then close it

    def feed():
        server.sendall(json.dumps(_added(FLASH_DEVICE)).encode() + b"\n")
        server.sendall(json.dumps({"type": "device_removed"}).encode() + b"\n")
        server.sendall(json.dumps(_added(FLASH_DEVICE)).encode() + b"\n")
        server.close()  # EOF ends the pump

    t = threading.Thread(target=feed)
    t.start()
    injected = sub.run()
    t.join()
    assert injected == 2
    assert len(sent) == 2
