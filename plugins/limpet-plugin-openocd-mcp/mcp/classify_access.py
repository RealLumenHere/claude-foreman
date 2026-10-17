#!/usr/bin/env python3
"""classify_access — pre-energize OPEN/LOCKED/DEAD debug-access classifier.

wq#4143: ports the pin/access classification algorithm from
BrownFineSecurity/iothackbot's `tools/iothackbot/core/jtagprobe_core.py`
(MIT License, see upstream LICENSE) into this plugin. Upstream drives SEGGER
J-Link via JLinkExe command scripts; this module reaches the same
classification using OpenOCD's Tcl RPC port instead, since that's the probe
surface this plugin already owns (see also ops/wq3881-openocd-mcp's client,
same wire protocol, verified in reports/wq3892_verify_wq3881_openocd_mcp.md).
The JEP106 vendor table, flash-base heuristics, vector-table plausibility
check, and the four-way classification decision are ported as-is; only the
probe transport changed.

    OPEN    - DP/TAP responds, core halts, memory reads return plausible data
    LOCKED  - DP/TAP responds, but halt or memory reads fail or return the
              readout-protection sentinel (0xFFFFFFFF / 0x00000000 only).
    DEAD    - No DP/TAP response at all. Debug fused off, pins not wired, or
              wrong target.

Declared `tools_energize.classify_access = false` in limpet-plugin.toml: it is
read-only recon run before an operator decides whether to energize destructive
tools (flash_write, halt, reset, ...) against the target, matching the
treatment already given to this plugin's other recon tools (jtag_scan,
identify_chip).

Stdlib-only on purpose — same rationale as ops/wq3881-openocd-mcp/openocd_client.py:
no `mcp` import, so this is unit-testable without the SDK being installed.
"""
from __future__ import annotations

import json
import socket
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

RPC_TERMINATOR = b"\x1a"

# JEP106 ARM manufacturer continuation/identity -> vendor name.
# Key format: (continuation_count, identity_code). Ported from
# jtagprobe_core.py JEP106_VENDORS.
JEP106_VENDORS: Dict[Tuple[int, int], str] = {
    (0, 0x20): "STMicroelectronics",
    (0, 0x15): "NXP",
    (0, 0x49): "Texas Instruments",
    (0, 0x17): "Texas Instruments",
    (0, 0x3B): "ARM",
    (0, 0x7F): "ARM",
    (4, 0x3B): "ARM",
    (4, 0x44): "Nordic Semiconductor",
    (0, 0x44): "Nordic Semiconductor",
    (0, 0x1F): "Atmel/Microchip",
    (0, 0x1B): "Cypress/Infineon",
    (0, 0x09): "Infineon",
    (0, 0x4B): "Espressif",
    (0, 0x6B): "Silicon Labs",
    (0, 0x21): "Renesas",
}

# Common flash bases by vendor identity, used for the memory-read sanity
# check. Ported from jtagprobe_core.py VENDOR_FLASH_BASES.
VENDOR_FLASH_BASES: Dict[str, List[int]] = {
    "STMicroelectronics": [0x08000000],
    "NXP": [0x00000000, 0x10000000],
    "Nordic Semiconductor": [0x00000000, 0x10001000],
    "Texas Instruments": [0x00000000],
    "Silicon Labs": [0x00000000],
    "Atmel/Microchip": [0x00000000],
    "Espressif": [0x40000000],
    "Renesas": [0x00000000],
}
DEFAULT_FLASH_BASES = [0x00000000, 0x08000000]
SRAM_PLAUSIBLE_RANGES = [
    (0x20000000, 0x20100000),  # Cortex-M default SRAM
    (0x10000000, 0x10100000),  # NXP/Nordic SRAM aliases
    (0x1FFF0000, 0x20000000),  # some STM32 boot SRAM
]
CPUID_ADDR = 0xE000ED00

# Vendor-specific protection hints, surfaced only when classification ==
# LOCKED. Ported from jtagprobe_core.py's inline protection_hint dict.
PROTECTION_HINTS: Dict[str, str] = {
    "STMicroelectronics": "STM32 RDP Level 1/2 (see RM, FLASH_OPTR bits 15:8).",
    "NXP": "LPC CRP1/CRP2 or Kinetis FSEC. Check vector 0x000002FC / FSEC byte.",
    "Nordic Semiconductor": "APPROTECT enabled in UICR. ERASEALL via CTRL-AP may recover.",
    "Texas Instruments": "MSP debug security or FlashSS lockout.",
    "Silicon Labs": "AAP lock / DEBUGLOCK page.",
    "Espressif": "ESP32 eFuse JTAG_DISABLE or secure boot.",
}


def decode_idcode(idcode: int) -> Dict[str, Any]:
    """Decode a JTAG IDCODE (IEEE 1149.1). Ported from jtagprobe_core.py."""
    version = (idcode >> 28) & 0xF
    partno = (idcode >> 12) & 0xFFFF
    manufacturer = (idcode >> 1) & 0x7FF
    continuation = (manufacturer >> 7) & 0xF
    identity = manufacturer & 0x7F
    vendor = JEP106_VENDORS.get((continuation, identity), "Unknown")
    return {
        "raw": f"0x{idcode:08X}",
        "version": version,
        "partno": f"0x{partno:04X}",
        "manufacturer_identity": f"0x{identity:02X}",
        "vendor": vendor,
    }


def looks_like_vector_table(words: Sequence[int]) -> bool:
    """Heuristic: a Cortex-M vector table starts with an initial SP (SRAM
    address) followed by a reset vector pointing into flash (LSB=1, Thumb).
    Ported from jtagprobe_core.py.
    """
    if len(words) < 2:
        return False
    sp, reset = words[0], words[1]
    in_sram = any(lo <= sp < hi for lo, hi in SRAM_PLAUSIBLE_RANGES)
    thumb = (reset & 1) == 1
    return in_sram and thumb


def classify_dp_access(
    dp_responds: bool,
    halted: bool,
    flash_reads: Sequence[Dict[str, Any]],
    vector_table_plausible: bool,
) -> Tuple[str, str]:
    """The four-way OPEN/LOCKED/DEAD decision. Ported verbatim (logic only;
    inputs renamed) from jtagprobe_core.py's Phase 4 ("classify").
    """
    if not dp_responds:
        return "DEAD", "No DP/TAP response on any tested interface."
    if vector_table_plausible and halted:
        return "OPEN", "Connected, halted core, and read a plausible vector table."
    if halted and flash_reads:
        sus = all(r["all_ones"] or r["all_zero"] for r in flash_reads)
        if sus:
            return "LOCKED", (
                "DP responded and core halted, but flash reads returned "
                "all-0xFF / all-0x00 (readout protection signature)."
            )
        return "OPEN", "Connected, halted, and read non-sentinel memory."
    if dp_responds:
        return "LOCKED", (
            "DP/TAP accessible but halt or memory read failed. "
            "Typical of RDP / CRP / APPROTECT engaged."
        )
    return "DEAD", "Indeterminate response from OpenOCD."


class OpenOCDProbeError(RuntimeError):
    """Raised when the Tcl RPC link misbehaves or can't be reached."""


class OpenOCDProbe:
    """Minimal read-only Tcl RPC client for pre-energize classification.

    Same wire protocol as ops/wq3881-openocd-mcp/openocd_client.py (command +
    0x1a terminator, one request at a time) — reimplemented compactly here so
    this plugin stays self-contained when installed standalone under
    /opt/limpet/plugins/openocd-mcp. Attaches to the Tcl RPC port the
    already-running openocd-mcp process (or mcjtag) manages; does not launch
    OpenOCD itself.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 6666, timeout: float = 5.0):
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        try:
            self._sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise OpenOCDProbeError(f"cannot reach OpenOCD Tcl RPC at {self._host}:{self._port}: {exc}") from exc

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _send(self, cmd: str) -> str:
        if self._sock is None:
            raise OpenOCDProbeError("not connected; call connect() first")
        self._sock.sendall(cmd.encode() + RPC_TERMINATOR)
        chunks: List[bytes] = []
        while True:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise OpenOCDProbeError("Tcl RPC connection closed unexpectedly")
            chunks.append(chunk)
            if chunks[-1].endswith(RPC_TERMINATOR):
                break
        return b"".join(chunks)[:-1].decode(errors="replace").strip()

    def scan_chain_idcodes(self) -> List[int]:
        """Parse `scan_chain` output for non-sentinel IDCODE hex values."""
        raw = self._send("scan_chain")
        idcodes: List[int] = []
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) < 4 or not fields[0].isdigit():
                continue
            try:
                idcode = int(fields[3], 16)
            except ValueError:
                continue
            if idcode not in (0, 0xFFFFFFFF):
                idcodes.append(idcode)
        return idcodes

    def halt_and_read(self, flash_bases: Sequence[int]) -> Tuple[bool, List[Dict[str, Any]]]:
        """Transient halt + memory peek at each candidate flash base. The
        probe does not resume the core; callers that need the target running
        again should issue `resume` themselves under the normal energize gate.
        """
        # OpenOCD prints "target halted due to ..." on a successful halt.
        halted = "halted" in self._send("halt").lower()
        reads: List[Dict[str, Any]] = []
        for base in flash_bases:
            resp = self._send(f"read_memory 0x{base:08x} 32 4")
            try:
                words = [int(tok, 0) for tok in resp.split()]
            except ValueError:
                words = []
            reads.append({
                "address": f"0x{base:08X}",
                "words": words,
                "all_ones": bool(words) and all(w == 0xFFFFFFFF for w in words),
                "all_zero": bool(words) and all(w == 0 for w in words),
                "vector_table_plausible": looks_like_vector_table(words),
            })
        return halted, reads


def classify_access(
    host: str = "127.0.0.1",
    port: int = 6666,
    flash_bases: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Top-level pre-energize classification. Connects read-only to the Tcl
    RPC port an already-running OpenOCD session exposes, scans the chain,
    does one transient halt + memory peek, and returns the OPEN/LOCKED/DEAD
    verdict plus the evidence behind it.
    """
    probe = OpenOCDProbe(host=host, port=port)
    try:
        probe.connect()
    except OpenOCDProbeError as exc:
        return {
            "classification": "DEAD",
            "classification_reason": str(exc),
            "vendor": "Unknown",
            "protection_hint": None,
            "evidence": {},
        }

    try:
        idcodes = probe.scan_chain_idcodes()
        dp_responds = bool(idcodes)
        identification = [decode_idcode(i) for i in idcodes]
        vendor = identification[0]["vendor"] if identification else "Unknown"

        halted = False
        flash_reads: List[Dict[str, Any]] = []
        vector_table_plausible = False
        if dp_responds:
            bases = list(flash_bases) if flash_bases else VENDOR_FLASH_BASES.get(vendor, DEFAULT_FLASH_BASES)
            try:
                halted, flash_reads = probe.halt_and_read(bases)
                vector_table_plausible = any(r["vector_table_plausible"] for r in flash_reads)
            except OpenOCDProbeError:
                pass  # halt/read failure is itself evidence -> falls through to LOCKED below

        classification, reason = classify_dp_access(dp_responds, halted, flash_reads, vector_table_plausible)
        protection_hint = PROTECTION_HINTS.get(vendor) if classification == "LOCKED" else None

        return {
            "classification": classification,
            "classification_reason": reason,
            "vendor": vendor,
            "protection_hint": protection_hint,
            "evidence": {
                "idcodes": [f"0x{i:08X}" for i in idcodes],
                "identification": identification,
                "halted": halted,
                "flash_reads": flash_reads,
            },
        }
    finally:
        probe.close()


if __name__ == "__main__":
    tcl_port = int(sys.argv[1]) if len(sys.argv) > 1 else 6666
    result = classify_access(port=tcl_port)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["classification"] != "DEAD" else 1)
