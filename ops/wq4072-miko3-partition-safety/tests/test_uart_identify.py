"""Tests for ops/wq4072-miko3-partition-safety/uart_identify.py (wq#4072).

No pyserial/hardware required: identify_ports() takes probe_port's behavior as given
via monkeypatch, so the aggregation logic is tested without a real serial device.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uart_identify as ui  # noqa: E402


def test_identify_ports_records_reply(monkeypatch):
    def fake_probe(port, baud, timeout_s=0.5):
        if port == "/dev/ttyS1" and baud == 115200:
            return b"FW v6.0\r\n"
        return b""

    monkeypatch.setattr(ui, "probe_port", fake_probe)
    results = ui.identify_ports(["/dev/ttyS0", "/dev/ttyS1"], baud_rates=(9600, 115200))

    assert "reply" in results["/dev/ttyS1"][115200]
    assert results["/dev/ttyS1"][115200]["reply"] == b"FW v6.0\r\n"
    assert "/dev/ttyS0" not in results or all("reply" not in v for v in results["/dev/ttyS0"].values())


def test_identify_ports_records_errors_without_raising(monkeypatch):
    def fake_probe(port, baud, timeout_s=0.5):
        raise OSError("Permission denied")

    monkeypatch.setattr(ui, "probe_port", fake_probe)
    results = ui.identify_ports(["/dev/ttyS0"], baud_rates=(9600,))

    assert results["/dev/ttyS0"][9600]["error"] == "Permission denied"
