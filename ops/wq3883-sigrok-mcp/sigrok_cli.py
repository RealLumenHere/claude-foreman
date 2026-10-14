#!/usr/bin/env python3
"""Pure argv-building and output-parsing for sigrok-cli (wq#3883).

No subprocess calls here -- that boundary lives in runner.py so this module
stays unit-testable without a real sigrok-cli binary or hardware attached.
Command shapes and output formats are pinned to sigrok-cli(1) man page
examples (--scan, --config samplerate=, -C channels, -P/-A decode, -O/-o
export) and the sigrok wiki's protocol-decoder output examples, not guessed.
"""

from __future__ import annotations

import re

_SAMPLERATE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kKmMgG]?)(?:[hH][zZ])?\s*$")
_SAMPLERATE_MULT = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}

# Pinned to sigrok-cli(1) --scan EXAMPLES:
#   demo - Demo device with 12 channels: D0 D1 D2 D3 D4 D5 D6 D7 A0 A1 A2 A3
#   fx2lafw:conn=3.26 - CWAV USBee SX with 8 channels: 0 1 2 3 4 5 6 7
#   Digitek DT4000ZC with 1 channel: P1   (driver already pinned by --driver)
_SCAN_LINE_RE = re.compile(
    r"^(?:(?P<driver_spec>[\w.+-]+(?::conn=\S+)?)\s+-\s+)?"
    r"(?P<description>.+?)\s+with\s+(?P<nchan>\d+)\s+channels?:\s+(?P<channels>.+)$"
)

# Pinned to sigrok wiki File_format:protocoldata / Protocol_decoder:I2c examples:
#   i2c-1: Address write: A0
#   uart-1: [0A]
_DECODE_LINE_RE = re.compile(r"^(?P<decoder>[A-Za-z0-9_]+)-(?P<row>\d+):\s*(?P<text>.*)$")


class SigrokCliError(ValueError):
    """Malformed input this module can reject without ever running sigrok-cli."""


def normalize_samplerate(raw) -> int:
    """Turn a user-given samplerate ('1MHz', '500 kHz', '1m', 1000000) into whole Hz.

    samplerate=1000000, samplerate=1m, and samplerate="1 MHz" are documented
    as equivalent on sigrok-cli(1); this always emits the plain-Hz form,
    which the man page confirms is accepted by every samplerate-capable
    driver.
    """
    if isinstance(raw, (int, float)):
        hz = float(raw)
    else:
        match = _SAMPLERATE_RE.match(str(raw))
        if not match:
            raise SigrokCliError(f"unrecognized samplerate: {raw!r}")
        value, suffix = match.groups()
        hz = float(value) * _SAMPLERATE_MULT[suffix.lower()]
    if hz <= 0:
        raise SigrokCliError(f"samplerate must be positive: {raw!r}")
    return int(hz)


def driver_arg(driver: str, conn: str = "", **driver_opts: str) -> str:
    """Build a --driver argument value: 'driver[:conn=X][:opt=val...]'."""
    if not driver:
        raise SigrokCliError("driver is required")
    parts = [driver]
    if conn:
        parts.append(f"conn={conn}")
    for key, val in driver_opts.items():
        parts.append(f"{key}={val}")
    return ":".join(parts)


def scan_argv(driver: str = "", conn: str = "") -> list:
    argv = ["sigrok-cli"]
    if driver:
        argv += ["--driver", driver_arg(driver, conn=conn)]
    argv += ["--scan"]
    return argv


def parse_scan_output(stdout: str) -> list:
    """Parse 'sigrok-cli --scan' output into structured device records."""
    devices = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("The following"):
            continue
        match = _SCAN_LINE_RE.match(line)
        if not match:
            continue
        driver_spec = match.group("driver_spec") or ""
        driver, _, conn_part = driver_spec.partition(":conn=")
        devices.append({
            "driver": driver,
            "conn": conn_part,
            "description": match.group("description"),
            "channel_count": int(match.group("nchan")),
            "channels": match.group("channels").split(),
        })
    return devices


def show_argv(driver: str = "", conn: str = "", decoder: str = "", input_file: str = "") -> list:
    argv = ["sigrok-cli"]
    if driver:
        argv += ["--driver", driver_arg(driver, conn=conn)]
    elif decoder:
        argv += ["--protocol-decoders", decoder]
    elif input_file:
        argv += ["--input-file", input_file]
    else:
        raise SigrokCliError("show requires driver, decoder, or input_file")
    argv += ["--show"]
    return argv


def capture_argv(
    driver: str,
    samplerate_hz: int,
    output_path: str,
    conn: str = "",
    channels: str = "",
    samples: str = "",
    duration: str = "",
    output_format: str = "",
) -> list:
    if samples and duration:
        raise SigrokCliError("specify samples or duration, not both")
    if not output_path:
        raise SigrokCliError("output_path is required")
    argv = [
        "sigrok-cli",
        "--driver", driver_arg(driver, conn=conn),
        "--config", f"samplerate={samplerate_hz}",
    ]
    if channels:
        argv += ["--channels", channels]
    if samples:
        argv += ["--samples", samples]
    elif duration:
        argv += ["--time", duration]
    if output_format:
        argv += ["--output-format", output_format]
    argv += ["--output-file", output_path]
    return argv


def decode_argv(input_file: str, decoders: str, annotations: str = "") -> list:
    if not input_file or not decoders:
        raise SigrokCliError("input_file and decoders are required")
    argv = ["sigrok-cli", "--input-file", input_file, "--protocol-decoders", decoders]
    if annotations:
        argv += ["--protocol-decoder-annotations", annotations]
    return argv


def parse_decode_output(stdout: str) -> list:
    """Parse annotation lines shaped '<decoder>-<row>: <text>'."""
    annotations = []
    for line in stdout.splitlines():
        match = _DECODE_LINE_RE.match(line.strip())
        if not match:
            continue
        annotations.append({
            "decoder": match.group("decoder"),
            "row": int(match.group("row")),
            "text": match.group("text"),
        })
    return annotations


def export_argv(input_file: str, output_path: str, output_format: str) -> list:
    if not (input_file and output_path and output_format):
        raise SigrokCliError("input_file, output_path, and output_format are required")
    return [
        "sigrok-cli",
        "--input-file", input_file,
        "--output-format", output_format,
        "--output-file", output_path,
    ]
