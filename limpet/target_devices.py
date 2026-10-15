"""wq#3940 — load/query the LIMPET HAL target device database and derive udev rules.

Thin reader for `target-devices.d/*.toml` (one file per vendor family, built from the
wq#3923 SoC boot-mode VID:PID research). This is what `ops/wq3809-limpet-hal/hal.py`'s
`target_device` plugin resolves a detected USB device against for real-time
soc_family/mode_name/recommended_tool enrichment, and what the generated udev rules at
`target-devices.d/91-limpet-targets.rules` are derived from — regenerate that file with
`render-udev-rules` below if the catalog changes; it is not hand-maintained in parallel.

Three match types, checked by `lookup()` in that order, same scheme as
`usb_boot_signatures.py`:
  vid_pid          exact (usb_vid, usb_pid) pair
  vid_pid_wildcard exact usb_vid, any usb_pid (OEM-identifies-but-does-not-mode-identify)
  usb_class        bInterfaceClass:bInterfaceSubClass:bInterfaceProtocol, independent of
                   usb_vid/usb_pid entirely (DFU, ADB, fastboot, CDC-ACM, RNDIS, mass storage)

CLI:
    python3 target_devices.py lookup <vid> <pid> [--class <cc:ss:pp>]
    python3 target_devices.py list [--file mediatek|qualcomm|...]
    python3 target_devices.py render-udev-rules
"""
from __future__ import annotations

import argparse
import json
import pathlib
import tomllib
from dataclasses import dataclass

CATALOG_DIR = pathlib.Path(__file__).parent / "target-devices.d"

CONFIDENCE_WEIGHT = {"high": 0.9, "medium": 0.6, "low": 0.3}


@dataclass(frozen=True)
class TargetDevice:
    match_type: str
    usb_vid: str
    usb_pid: str
    class_code: str
    vendor: str
    soc_family: str
    soc_model: str
    mode_name: str
    recommended_tool: str
    confidence: str
    notes: str
    source_file: str
    # wq#3935 (ewf absorption): optional per-device flashing profile. Empty on
    # entries that haven't been profiled yet; populated per-entry in the TOML.
    # `partition_layout` names a [partition_layouts.<name>] table (same catalog
    # dir, loaded by load_partition_layouts) rather than inlining the layout,
    # because every SoC generation of a vendor tends to share one of a few
    # layouts and duplicating it per entry invites drift.
    recommended_commands: tuple = ()
    partition_layout: str = ""


def load_catalog(catalog_dir: pathlib.Path = CATALOG_DIR) -> list[TargetDevice]:
    """Every [[device]] table from every *.toml in catalog_dir, in filename order."""
    devices = []
    for toml_path in sorted(catalog_dir.glob("*.toml")):
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        for entry in data.get("device", []):
            if "recommended_commands" in entry:
                entry["recommended_commands"] = tuple(entry["recommended_commands"])
            devices.append(TargetDevice(**entry, source_file=toml_path.stem))
    return devices


def load_partition_layouts(catalog_dir: pathlib.Path = CATALOG_DIR) -> dict:
    """Every [partition_layouts.<name>] table from every *.toml in catalog_dir.

    Returns name -> layout dict (the TOML table plus `source_file`). A layout
    name reused across files is a catalog bug, not a merge: fail loud so the
    collision is fixed in the TOML rather than silently last-writer-wins.
    """
    layouts = {}
    for toml_path in sorted(catalog_dir.glob("*.toml")):
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        for name, layout in data.get("partition_layouts", {}).items():
            if name in layouts:
                raise ValueError(
                    f"partition layout {name!r} defined in both "
                    f"{layouts[name]['source_file']}.toml and {toml_path.name}")
            layouts[name] = {**layout, "source_file": toml_path.stem}
    return layouts


def lookup(
    vid: str,
    pid: str,
    class_code: str | None = None,
    devices: list[TargetDevice] | None = None,
) -> list[TargetDevice]:
    """Every device matching this (vid, pid[, class_code]), highest-confidence first.

    `vid`/`pid` are lowercase hex strings (no "0x"); `class_code` is an optional
    "cc:ss:pp" hex triplet read from the USB interface descriptor.
    """
    if devices is None:
        devices = load_catalog()
    vid, pid = vid.lower(), pid.lower()
    hits = []
    for d in devices:
        if d.match_type == "vid_pid" and d.usb_vid == vid and d.usb_pid == pid:
            hits.append(d)
        elif d.match_type == "vid_pid_wildcard" and d.usb_vid == vid:
            hits.append(d)
        elif d.match_type == "usb_class" and class_code is not None and d.class_code == class_code.lower():
            hits.append(d)
    return sorted(hits, key=lambda d: CONFIDENCE_WEIGHT.get(d.confidence, 0.0), reverse=True)


_RULE_HEADER = """\
# /etc/udev/rules.d/91-limpet-targets.rules
#
# GENERATED from limpet/target-devices.d/*.toml by
# `python3 limpet/target_devices.py render-udev-rules` — do not hand-edit, regenerate
# instead. One rule per cataloged vid_pid/vid_pid_wildcard device and one interface-level
# rule per usb_class signature. Every match tags the device `limpet_target` and sets
# ENV{LIMPET_TARGET_MODE} so ops/wq3809-limpet-hal's HAL daemon can pick up the plug
# event in real time instead of polling `lsusb`.
"""


def render_udev_rules(devices: list[TargetDevice] | None = None) -> str:
    if devices is None:
        devices = load_catalog()
    lines = [_RULE_HEADER]
    seen = set()
    for d in devices:
        mode_slug = "".join(c if c.isalnum() else "_" for c in d.mode_name.lower())[:48]
        if d.match_type == "vid_pid":
            key = ("vid_pid", d.usb_vid, d.usb_pid)
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{d.usb_vid}", ATTR{{idProduct}}=="{d.usb_pid}", '
                f'TAG+="limpet_target", ENV{{LIMPET_TARGET_MODE}}="{mode_slug}"'
            )
        elif d.match_type == "vid_pid_wildcard":
            key = ("vid_pid_wildcard", d.usb_vid)
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="{d.usb_vid}", '
                f'TAG+="limpet_target", ENV{{LIMPET_TARGET_MODE}}="{mode_slug}"'
            )
        elif d.match_type == "usb_class":
            key = ("usb_class", d.class_code)
            if key in seen:
                continue
            seen.add(key)
            cc, ss, pp = d.class_code.split(":")
            lines.append(
                f'SUBSYSTEM=="usb", ENV{{DEVTYPE}}=="usb_interface", '
                f'ATTRS{{bInterfaceClass}}=="{cc}", ATTRS{{bInterfaceSubClass}}=="{ss}", '
                f'ATTRS{{bInterfaceProtocol}}=="{pp}", TAG+="limpet_target", '
                f'ENV{{LIMPET_TARGET_MODE}}="{mode_slug}"'
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    lookup_parser = sub.add_parser("lookup", help="resolve a VID:PID (+ optional class) against the catalog")
    lookup_parser.add_argument("vid")
    lookup_parser.add_argument("pid")
    lookup_parser.add_argument("--class", dest="class_code", default=None, help="cc:ss:pp hex triplet")

    list_parser = sub.add_parser("list", help="print every cataloged device")
    list_parser.add_argument("--file", dest="file", default=None, help="restrict to one vendor file stem, e.g. mediatek")

    sub.add_parser("render-udev-rules", help="print the generated 91-limpet-targets.rules content")

    args = parser.parse_args(argv)
    devices = load_catalog()

    if args.command == "lookup":
        hits = lookup(args.vid, args.pid, args.class_code, devices)
        print(json.dumps([vars(h) for h in hits], indent=2))
    elif args.command == "list":
        rows = devices if args.file is None else [d for d in devices if d.source_file == args.file]
        print(json.dumps([vars(d) for d in rows], indent=2))
    elif args.command == "render-udev-rules":
        print(render_udev_rules(devices), end="")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
