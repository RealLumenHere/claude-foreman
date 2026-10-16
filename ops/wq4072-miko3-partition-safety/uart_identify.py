#!/usr/bin/env python3
"""uart_identify.py — wq#4072 step 5.

Identifies which /dev/ttyS* node is the Miko 3 Base board motor/charger/sensor UART,
by sending the single-command query token `GETFV` (get firmware version) confirmed in
agents/work-queue/reports/223_miko_mcu_disasm.md as part of the base board's live
5-char command protocol (bootloader-mode framing documents the same command set) and
listening for any reply. GETFV is a read-only status query in that protocol — this
never sends a motor/flash/program command (MTRLP/MTRRP/PRGFL/etc. are deliberately not
in this script).

Not run by the staging session — no bench host / hardware in this sandbox's execution
path (same gap as wq#349). Run on the bench host once the Miko 3 is wired at 1.8V:
    ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4072-miko3-partition-safety/uart_identify.py'

Requires pyserial (`pip install pyserial`, same dependency wq#349's miko_uart.py PoC
already calls out).
"""
from __future__ import annotations

import argparse
import glob
import sys
import time

QUERY = b"GETFV\r\n"
BAUD_RATES = (9600, 19200, 38400, 57600, 115200)


def probe_port(port: str, baud: int, timeout_s: float = 0.5) -> bytes:
    import serial  # pyserial; deferred import so --help works without the dependency

    with serial.Serial(port, baudrate=baud, timeout=timeout_s) as ser:
        ser.reset_input_buffer()
        ser.write(QUERY)
        time.sleep(timeout_s)
        return ser.read(256)


def identify_ports(ports: list[str], baud_rates: tuple[int, ...] = BAUD_RATES) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for port in ports:
        for baud in baud_rates:
            try:
                reply = probe_port(port, baud)
            except Exception as e:  # device busy, permission, no such port, etc.
                results.setdefault(port, {})[baud] = {"error": str(e)}
                continue
            if reply:
                results.setdefault(port, {})[baud] = {"reply": reply}
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port-glob", default="/dev/ttyS*", help="glob for candidate ports (default: /dev/ttyS*)")
    ap.add_argument("--baud", type=int, action="append", dest="bauds", help="baud rate to try (repeatable; default: common set)")
    args = ap.parse_args()

    ports = sorted(glob.glob(args.port_glob))
    if not ports:
        print(f"no ports matched {args.port_glob!r}")
        return 1

    baud_rates = tuple(args.bauds) if args.bauds else BAUD_RATES
    results = identify_ports(ports, baud_rates)

    found_any_reply = False
    for port in ports:
        for baud, outcome in results.get(port, {}).items():
            if "reply" in outcome:
                found_any_reply = True
                print(f"{port} @ {baud}: REPLY {outcome['reply']!r}")
            elif "error" in outcome:
                print(f"{port} @ {baud}: ERROR {outcome['error']}")
        if port not in results:
            print(f"{port}: no reply at any tried baud rate")

    if not found_any_reply:
        print("\nno port replied to GETFV — base-board UART not found on this glob/baud set")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
