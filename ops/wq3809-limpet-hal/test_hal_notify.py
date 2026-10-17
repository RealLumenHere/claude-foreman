"""wq#4112 -- tests for the HAL notification path.

Tests the limpet-hal-notify -> hal_daemon pipeline using real Unix sockets
in a temp directory (no udev, no root, no real USB bus needed).
"""
import importlib
import json
import os
import socket
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limpet_hal_notify  # noqa: E402


@pytest.fixture
def tmp_runtime(tmp_path):
    """Set up a temp runtime dir with socket paths for testing."""
    sock_path = str(tmp_path / "hal.sock")
    event_path = str(tmp_path / "hal-events.sock")
    inv_path = str(tmp_path / "hardware.json")
    os.environ["LIMPET_HAL_SOCKET"] = sock_path
    os.environ["LIMPET_HAL_EVENT_SOCKET"] = event_path
    os.environ["LIMPET_INVENTORY"] = inv_path
    yield {
        "sock": sock_path,
        "event": event_path,
        "inventory": inv_path,
        "dir": str(tmp_path),
    }
    for k in ("LIMPET_HAL_SOCKET", "LIMPET_HAL_EVENT_SOCKET", "LIMPET_INVENTORY"):
        os.environ.pop(k, None)


def test_notify_send_event_returns_false_when_no_daemon(tmp_path):
    """limpet-hal-notify should fail silently when the daemon is not running."""
    os.environ["LIMPET_HAL_SOCKET"] = str(tmp_path / "nonexistent.sock")
    result = limpet_hal_notify.send_event("add", "/devices/usb1", "1234", "5678")
    assert result is False
    os.environ.pop("LIMPET_HAL_SOCKET", None)


def test_notify_send_event_delivers_to_dgram_socket(tmp_runtime):
    """limpet-hal-notify delivers a JSON event when the socket exists."""
    recv_sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    recv_sock.bind(tmp_runtime["sock"])
    recv_sock.settimeout(2.0)

    result = limpet_hal_notify.send_event("add", "/devices/usb1/1-2", "05c6", "9008")
    assert result is True

    data, _ = recv_sock.recvfrom(4096)
    event = json.loads(data.decode("utf-8"))
    assert event["action"] == "add"
    assert event["devpath"] == "/devices/usb1/1-2"
    assert event["vid"] == "05c6"
    assert event["pid"] == "9008"
    recv_sock.close()


def test_notify_cli_main(tmp_path):
    """CLI entry point should return 0 even with bad args (silent for udev)."""
    os.environ["LIMPET_HAL_SOCKET"] = str(tmp_path / "nonexistent.sock")
    assert limpet_hal_notify.main([]) == 0
    assert limpet_hal_notify.main(["add", "/dev/usb1"]) == 0
    os.environ.pop("LIMPET_HAL_SOCKET", None)


def test_daemon_status_no_socket(tmp_path):
    """hal_daemon status returns 1 when no socket exists."""
    os.environ["LIMPET_HAL_SOCKET"] = str(tmp_path / "nonexistent.sock")
    import hal_daemon
    assert hal_daemon.main(["status"]) == 1
    os.environ.pop("LIMPET_HAL_SOCKET", None)
