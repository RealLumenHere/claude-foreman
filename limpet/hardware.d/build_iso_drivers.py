"""ISO build helper for wq#3924: scans the hardware driver catalog
(limpet/hardware.d/*.toml) and emits what the Limpet ISO build needs to
pre-bake so every cataloged RE device works offline, out of the box --
no network needed at install time to `apt install` a DKMS module or
download a firmware blob.

Two outputs, both derived straight from the catalog (never hand-maintained
in parallel with it):
  - DKMS modules to pre-compile into the ISO (dkms=true entries)
  - firmware packages to bundle (firmware_blob=true entries with a named
    firmware_package)

Pure: reads the catalog dir and returns data; the caller decides what to do
with it (print, write a manifest, feed a mkosi/live-build hook). No
filesystem writes here, no subprocess calls -- this is a planning step, not
the ISO build itself.
"""
from __future__ import annotations

import dataclasses
import pathlib
import tomllib

CATALOG_DIR = pathlib.Path(__file__).parent


@dataclasses.dataclass(frozen=True)
class DkmsTarget:
    device: str
    module: str
    source: str
    build_estimate_minutes: int


@dataclasses.dataclass(frozen=True)
class FirmwareTarget:
    device: str
    package: str
    note: str


def load_catalog(catalog_dir: pathlib.Path = CATALOG_DIR) -> list[dict]:
    """Every [[device]] table from every *.toml in catalog_dir, in filename order."""
    devices = []
    for toml_path in sorted(catalog_dir.glob("*.toml")):
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        devices.extend(data.get("device", []))
    return devices


def dkms_targets(devices: list[dict]) -> list[DkmsTarget]:
    return [
        DkmsTarget(
            device=d["name"],
            module=d["linux_driver"],
            source=d.get("dkms_source", ""),
            build_estimate_minutes=d.get("dkms_build_estimate_minutes", 0),
        )
        for d in devices
        if d.get("dkms")
    ]


def firmware_targets(devices: list[dict]) -> list[FirmwareTarget]:
    return [
        FirmwareTarget(
            device=d["name"],
            package=d["firmware_package"],
            note=d.get("firmware_note", ""),
        )
        for d in devices
        if d.get("firmware_blob") and d.get("firmware_package", "none") != "none"
    ]


def render_report(devices: list[dict]) -> str:
    dkms = dkms_targets(devices)
    fw = firmware_targets(devices)
    lines = ["# Limpet ISO hardware pre-bake plan (generated from limpet/hardware.d/*.toml)", ""]
    lines.append(f"## DKMS modules to pre-compile ({len(dkms)})")
    total_minutes = sum(t.build_estimate_minutes for t in dkms)
    for t in dkms:
        lines.append(f"- {t.module}  <- {t.device}  ({t.source})  ~{t.build_estimate_minutes} min")
    lines.append(f"  total estimated build time: ~{total_minutes} min")
    lines.append("")
    lines.append(f"## Firmware/package targets to bundle ({len(fw)})")
    for t in sorted(fw, key=lambda t: t.package):
        lines.append(f"- {t.package}  <- {t.device}")
        if t.note:
            lines.append(f"    {t.note}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_report(load_catalog()))
