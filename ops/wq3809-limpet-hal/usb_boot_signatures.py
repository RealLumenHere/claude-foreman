"""usb_boot_signatures.py — load/query the wq#3923 SoC boot-mode VID:PID map.

Thin reader for `usb_boot_signatures.toml` (the comprehensive VID:PID -> {vendor,
soc_family, mode_name, recommended_tool, confidence, notes} map researched for
wq#3923). This is the data source the HAL `target_device` plugin
(`hal.py:REGISTRY`) and the Target Device Wizard's IDENTIFY stage
(`docs/target-wizard-spec.md:144-156`) should resolve a VID:PID or USB interface
class against, instead of each caller hand-rolling its own lookup table.

Three match types, checked by `lookup()` in that order:
  vid_pid          exact (vid, pid) pair
  vid_pid_wildcard exact vid, any pid (OEM-identifies-but-does-not-mode-identify)
  usb_class        bInterfaceClass:bInterfaceSubClass:bInterfaceProtocol, independent
                   of vid/pid entirely (DFU, ADB, fastboot, CDC-ACM, RNDIS, mass storage)

CLI:
    python3 usb_boot_signatures.py lookup <vid> <pid> [--class <cc:ss:pp>]
    python3 usb_boot_signatures.py list [--match-type vid_pid|vid_pid_wildcard|usb_class]
"""
from __future__ import annotations

import argparse
import json
import os
import tomllib
from dataclasses import dataclass

SIGNATURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "usb_boot_signatures.toml")


@dataclass(frozen=True)
class BootSignature:
    match_type: str
    vid: str
    pid: str
    class_code: str
    vendor: str
    soc_family: str
    mode_name: str
    recommended_tool: str
    confidence: float
    notes: str


def load_signatures(path: str = SIGNATURES_PATH) -> list[BootSignature]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return [BootSignature(**entry) for entry in data["entry"]]


def lookup(vid: str, pid: str, class_code: str | None = None, signatures: list[BootSignature] | None = None) -> list[BootSignature]:
    """Return every signature matching this device, highest-confidence first.

    `vid`/`pid` are lowercase hex strings (no "0x"); `class_code` is an optional
    "cc:ss:pp" hex triplet read from the USB interface descriptor.
    """
    if signatures is None:
        signatures = load_signatures()
    vid, pid = vid.lower(), pid.lower()
    hits = []
    for sig in signatures:
        if sig.match_type == "vid_pid" and sig.vid == vid and sig.pid == pid:
            hits.append(sig)
        elif sig.match_type == "vid_pid_wildcard" and sig.vid == vid:
            hits.append(sig)
        elif sig.match_type == "usb_class" and class_code is not None and sig.class_code == class_code.lower():
            hits.append(sig)
    return sorted(hits, key=lambda s: s.confidence, reverse=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    lookup_parser = sub.add_parser("lookup", help="resolve a VID:PID (+ optional class) against the signature map")
    lookup_parser.add_argument("vid")
    lookup_parser.add_argument("pid")
    lookup_parser.add_argument("--class", dest="class_code", default=None, help="cc:ss:pp hex triplet")

    list_parser = sub.add_parser("list", help="print every signature")
    list_parser.add_argument("--match-type", choices=["vid_pid", "vid_pid_wildcard", "usb_class"], default=None)

    args = parser.parse_args(argv)
    signatures = load_signatures()

    if args.command == "lookup":
        hits = lookup(args.vid, args.pid, args.class_code, signatures)
        print(json.dumps([vars(h) for h in hits], indent=2))
    elif args.command == "list":
        rows = signatures if args.match_type is None else [s for s in signatures if s.match_type == args.match_type]
        print(json.dumps([vars(s) for s in rows], indent=2))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
