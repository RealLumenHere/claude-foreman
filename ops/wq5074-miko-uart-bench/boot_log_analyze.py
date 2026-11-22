#!/usr/bin/env python3
"""boot_log_analyze.py — wq#5074 Miko 3 head-board (EMT6736A V1.0, MT8167) UART identification.

Classifies a raw capture taken on a candidate GMR1/GMR2 pad during a Miko power cycle, so the
bench session answers three questions without eyeballing hex:

  1. Is anything there at all?          verdict no_data / stuck_line  -> wrong pad, RX pad, or no GND
  2. Is it the right baud?              verdict baud_mismatch          -> try the next rate in SWEEP
  3. Which boot stage spoke?            stages: preloader / tf-a / lk / kernel / android / shell

Input is a raw byte file (serial-mcp `get_log`/`capture_boot` output saved to disk, or a
`--capture` taken here). `--capture` only READS the port: it never writes a byte and holds
DTR/RTS deasserted, so it cannot drive a Miko pad. See RUNBOOK.md for the wiring order.

    python3 boot_log_analyze.py capture.bin --baud 921600
    python3 boot_log_analyze.py --capture /dev/ttyUSB0 --baud 921600 --seconds 30 --save cap.bin

Exit codes: 0 clean, 1 no_data/stuck_line, 2 baud_mismatch/noisy, 3 usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

# 921600 is the ticket's rate; the rest are the rates MediaTek boot chains and FT232R cover.
SWEEP = (921600, 115200, 460800, 1500000, 57600, 230400)

PRINTABLE = frozenset(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D}
CLEAN_RATIO = 0.85
MISMATCH_RATIO = 0.60
MIN_BYTES = 32  # below this a ratio is not evidence of anything
STUCK_RATIO = 0.90

# (stage, marker) — ordered by boot sequence. Case-sensitive, matched on decoded text.
STAGE_MARKERS = (
    ("preloader", "[PLFM]"),
    ("preloader", "[BLDR]"),
    ("preloader", "[PART]"),
    ("preloader", "[EMI]"),
    ("preloader", "Preloader"),
    ("preloader", "PRELOADER"),
    ("tf-a", "BL31"),
    ("tf-a", "ATF"),
    ("lk", "[LK]"),
    ("lk", "LK_"),
    ("lk", "lk boot"),
    ("lk", "[BOOT_TAG]"),
    ("kernel", "Booting Linux"),
    ("kernel", "Linux version"),
    ("kernel", "[    0.000000]"),
    ("android", "init: "),
    ("android", "init first stage"),
    ("android", "SELinux"),
    ("shell", "login:"),
    ("shell", "root@"),
)
STAGE_ORDER = ("preloader", "tf-a", "lk", "kernel", "android", "shell")


def printable_ratio(data: bytes) -> float:
    if not data:
        return 0.0
    return sum(1 for b in data if b in PRINTABLE) / len(data)


def stuck_byte(data: bytes) -> int | None:
    """0x00 dominating = line held low (break / Miko RX pad / missing pull); 0xFF = idle glitches."""
    if len(data) < MIN_BYTES:
        return None
    for b in (0x00, 0xFF):
        if data.count(b) / len(data) >= STUCK_RATIO:
            return b
    return None


def find_stages(text: str) -> list[str]:
    hit = {stage for stage, marker in STAGE_MARKERS if marker in text}
    return [s for s in STAGE_ORDER if s in hit]


def next_bauds(current: int | None) -> list[int]:
    return [b for b in SWEEP if b != current]


def analyze(data: bytes, baud: int | None = None) -> dict:
    ratio = printable_ratio(data)
    text = data.decode("ascii", errors="replace")
    stages = find_stages(text)
    stuck = stuck_byte(data)

    if not data:
        verdict, advice = "no_data", (
            "nothing received: wrong pad (or Miko's RX pad), host RX not on adapter MISO path, "
            "missing common GND, or adapter unpowered — move the probe to the other GMR pad")
    elif stuck is not None:
        verdict, advice = "stuck_line", (
            f"{stuck:#04x} dominates: line held {'low' if stuck == 0 else 'high/glitching'} — "
            "likely Miko's RX pad or a break condition, not a TX; try the other pad")
    elif stages and ratio >= MISMATCH_RATIO:
        verdict, advice = "clean", "boot log identified: this pad is Miko TX at this baud"
    elif len(data) < MIN_BYTES:
        verdict, advice = "noisy", "too few bytes to judge; recapture across a full power cycle"
    elif ratio >= CLEAN_RATIO:
        verdict, advice = "clean", "readable text but no known boot marker; save it and read it"
    elif ratio < MISMATCH_RATIO:
        verdict, advice = "baud_mismatch", (
            f"mostly non-printable at {baud or 'unknown'} baud — this pad is likely TX at another "
            f"rate; next: {next_bauds(baud)[:3]}")
    else:
        verdict, advice = "noisy", (
            "partly readable: suspect marginal level shifting, a floating GND, or a near-miss baud")

    return {
        "bytes": len(data),
        "baud": baud,
        "printable_ratio": round(ratio, 3),
        "stuck_byte": stuck,
        "stages": stages,
        "verdict": verdict,
        "advice": advice,
        "head": text[:200],
    }


EXIT = {"clean": 0, "no_data": 1, "stuck_line": 1, "baud_mismatch": 2, "noisy": 2}


def capture(port: str, baud: int, seconds: float) -> bytes:
    """Read-only capture: never writes, DTR/RTS held deasserted before the port opens."""
    import serial  # pyserial; deferred so analysis and --help work without it

    ser = serial.Serial()
    ser.port, ser.baudrate = port, baud
    ser.bytesize, ser.parity, ser.stopbits = serial.EIGHTBITS, serial.PARITY_NONE, serial.STOPBITS_ONE
    ser.timeout, ser.xonxoff, ser.rtscts, ser.dsrdtr = 0.2, False, False, False
    ser.dtr = ser.rts = False
    buf = bytearray()
    ser.open()
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            buf += ser.read(4096)
    finally:
        ser.close()
    return bytes(buf)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", help="raw capture file to analyze")
    ap.add_argument("--baud", type=int, default=None, help="baud the capture was taken at (921600 per ticket)")
    ap.add_argument("--capture", metavar="PORT", help="read-only capture from PORT instead of a file")
    ap.add_argument("--seconds", type=float, default=30.0, help="capture window (default 30s; power-cycle Miko inside it)")
    ap.add_argument("--save", metavar="PATH", help="write the raw capture here")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    if bool(args.file) == bool(args.capture):
        ap.error("give exactly one of FILE or --capture PORT")
    try:
        if args.capture:
            baud = args.baud or SWEEP[0]
            print(f"capturing {args.capture} @ {baud} 8N1 for {args.seconds:g}s — power-cycle Miko now",
                  file=sys.stderr)
            data = capture(args.capture, baud, args.seconds)
            args.baud = baud
        else:
            with open(args.file, "rb") as fh:
                data = fh.read()
        if args.save:
            with open(args.save, "wb") as fh:
                fh.write(data)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    result = analyze(data, args.baud)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['verdict']}: {result['bytes']} bytes, printable {result['printable_ratio']}, "
              f"stages {result['stages'] or '-'}")
        print(result["advice"])
    return EXIT[result["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
