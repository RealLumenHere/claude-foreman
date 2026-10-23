#!/usr/bin/env python3
"""wq#4523 -- installer-presence gate for the built Limpet ISO.

limpet-20261021.iso shipped with "no working installer" (P0). The validation
that found it grepped the casper layers for the *ubiquity* binary -- but
ubiquity does not exist on ANY stock Xubuntu 26.04/26.10 ISO: Canonical
retired it fleet-wide, and the flavors now install through the
`ubuntu-desktop-bootstrap` snap, seeded under /var/lib/snapd/seed/ in the
live-only casper layer (research/WQ3983_XUBUNTU_26_10_DISSECTION.md section 1,
confirmed against both the 26.04.1 and 26.10 manifests; the live session's
"Install" icon is a snap desktop entry, X-SnapInstanceName=
ubuntu-desktop-bootstrap). "Add ubiquity back" is therefore not a thing that
can be done -- it was never there. What CAN regress, and what this gate
checks on the finished artifact (not the build inputs -- wq#4456 showed those
drift), is everything the stock installer needs to exist and launch:

  1. /casper/install-sources.yaml is present, parses, is fsimage-layered,
     and names exactly one default source.
  2. Every squashfs layer the manifest references (and each parent in the
     dotted layer chain) is actually on the ISO.
  3. The live layer (<default layer>.live.squashfs) is on the ISO -- it is
     the only layer carrying the installer.
  4. The live layer's snap seed exists (var/lib/snapd/seed/seed.yaml) and
     both seeds the installer snap by name in seed.yaml AND carries its
     .snap file. Without this, casper still draws the "Install" icon but
     snapd has nothing to seed: the exact ghost-icon symptom the validation
     reported.

Exit 0 with {"ok": true, ...} JSON on stdout, exit 2 with every failure
listed otherwise. Needs xorriso + unsquashfs on PATH, no root.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

DEFAULT_INSTALLER_SNAP = "ubuntu-desktop-bootstrap"
SEED_DIR = "var/lib/snapd/seed"


def run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def iso_casper_files(iso: Path) -> set:
    """Names of regular files under /casper on the ISO."""
    r = run(["xorriso", "-indev", str(iso), "-find", "/casper", "-type", "f"])
    if r.returncode != 0:
        raise RuntimeError(f"xorriso cannot read {iso}: {r.stderr.strip()}")
    names = set()
    for line in r.stdout.splitlines():
        line = line.strip().strip("'")
        if line.startswith("/casper/"):
            names.add(line[len("/casper/"):])
    return names


def iso_extract(iso: Path, iso_path: str, dest: Path) -> Path:
    r = run(["xorriso", "-osirrox", "on", "-indev", str(iso),
             "-extract", iso_path, str(dest)])
    if r.returncode != 0:
        raise RuntimeError(f"cannot extract {iso_path} from {iso}: {r.stderr.strip()}")
    return dest


def squash_listing(sq: Path) -> set:
    r = run(["unsquashfs", "-l", str(sq)])
    if r.returncode != 0:
        raise RuntimeError(f"unsquashfs -l {sq} failed: {r.stderr.strip()}")
    prefix = "squashfs-root/"
    return {l[len(prefix):] for l in r.stdout.splitlines() if l.startswith(prefix)}


def squash_read(sq: Path, member: str, workdir: Path) -> str:
    dest = workdir / "seed-extract"
    r = run(["unsquashfs", "-d", str(dest), str(sq), member])
    extracted = dest / member
    if r.returncode != 0 or not extracted.is_file():
        raise RuntimeError(f"cannot extract {member} from {sq}: {r.stderr.strip()}")
    return extracted.read_text()


def parent_chain(layer: str) -> list:
    """minimal.standard -> [minimal]; dotted casper layer naming."""
    parts = layer.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def verify(iso: Path, installer_snap: str, workdir: Path) -> dict:
    errors = []
    report = {"iso": str(iso), "installer_snap": installer_snap}

    casper = iso_casper_files(iso)

    # 1. install-sources.yaml -- the installer's own manifest of what it can
    # put on disk. Missing/unparseable means no install source at all.
    if "install-sources.yaml" not in casper:
        errors.append("/casper/install-sources.yaml missing from ISO -- the installer has no source manifest")
        return {**report, "ok": False, "errors": errors}
    doc = yaml.safe_load(
        iso_extract(iso, "/casper/install-sources.yaml",
                    workdir / "install-sources.yaml").read_text())
    sources = (doc or {}).get("sources") or []
    if not sources:
        errors.append("install-sources.yaml lists no sources")
    defaults = [s for s in sources if s.get("default")]
    if len(defaults) != 1:
        errors.append(f"install-sources.yaml must mark exactly one default source, found {len(defaults)}")
    for s in sources:
        if s.get("type") != "fsimage-layered":
            errors.append(f"source {s.get('id')!r} has type {s.get('type')!r}, expected fsimage-layered")

    # 2. Every referenced layer squashfs, plus each parent in the dotted
    # chain, must be on the ISO.
    layers = []
    for s in sources:
        path = s.get("path") or ""
        if not path.endswith(".squashfs"):
            errors.append(f"source {s.get('id')!r} path {path!r} is not a .squashfs")
            continue
        layer = path[: -len(".squashfs")]
        layers.append((s, layer))
        for need in parent_chain(layer) + [layer]:
            if f"{need}.squashfs" not in casper:
                errors.append(f"/casper/{need}.squashfs missing (needed by source {s.get('id')!r})")

    if errors:
        return {**report, "ok": False, "errors": errors}

    # 3. The live layer: <default layer>.live on top of the default source.
    # It is the only layer that carries the installer (the installed system
    # never gets it).
    default_layer = defaults[0]["path"][: -len(".squashfs")]
    live_layer = f"{default_layer}.live"
    report["live_layer"] = live_layer
    if f"{live_layer}.squashfs" not in casper:
        errors.append(f"/casper/{live_layer}.squashfs missing -- no live layer means no live session and no installer")
        return {**report, "ok": False, "errors": errors}

    # 4. The snap seed inside the live layer. casper's "Install" icon is a
    # snap desktop entry; with no seeded installer snap it is a ghost.
    live_sq = iso_extract(iso, f"/casper/{live_layer}.squashfs", workdir / "live.squashfs")
    listing = squash_listing(live_sq)
    seed_yaml = f"{SEED_DIR}/seed.yaml"
    if seed_yaml not in listing:
        errors.append(f"{seed_yaml} missing from {live_layer}.squashfs -- snapd has nothing to seed in the live session")
        return {**report, "ok": False, "errors": errors}

    snap_files = sorted(
        p for p in listing
        if p.startswith(f"{SEED_DIR}/snaps/{installer_snap}_") and p.endswith(".snap"))
    if not snap_files:
        errors.append(
            f"no {SEED_DIR}/snaps/{installer_snap}_*.snap in {live_layer}.squashfs -- "
            f"the installer snap is not shipped")
    seed = yaml.safe_load(squash_read(live_sq, seed_yaml, workdir)) or {}
    seeded = {s.get("name") for s in seed.get("snaps") or []}
    if installer_snap not in seeded:
        errors.append(
            f"seed.yaml in {live_layer}.squashfs does not seed {installer_snap!r} "
            f"(seeds: {sorted(seeded)})")

    report["seed_snap_files"] = snap_files
    report["ok"] = not errors
    if errors:
        report["errors"] = errors
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True, type=Path, help="built ISO to verify")
    ap.add_argument("--installer-snap", default=DEFAULT_INSTALLER_SNAP,
                    help=f"seeded installer snap name (default: {DEFAULT_INSTALLER_SNAP})")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="scratch dir for extractions (default: a TemporaryDirectory)")
    args = ap.parse_args(argv)

    if not args.iso.is_file():
        print(json.dumps({"ok": False, "errors": [f"no such ISO: {args.iso}"]}))
        return 2

    try:
        if args.workdir:
            args.workdir.mkdir(parents=True, exist_ok=True)
            report = verify(args.iso, args.installer_snap, args.workdir)
        else:
            with tempfile.TemporaryDirectory(prefix="verify-installer.") as td:
                report = verify(args.iso, args.installer_snap, Path(td))
    except RuntimeError as e:
        print(json.dumps({"ok": False, "errors": [str(e)]}))
        return 2

    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
