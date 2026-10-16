#!/usr/bin/env python3
"""Layered-squashfs helpers for build.sh (wq#4077).

Xubuntu 26.04's ISO does not ship one casper/filesystem.squashfs. It ships
stacked layers -- minimal -> minimal.standard -> minimal.standard.live -- and
casper/install-sources.yaml (type fsimage-layered) names which layer the
installer copies to disk. A layer "a.b.c" sits on top of "a.b" and "a", the
naming livecd-rootfs uses (live-build/functions get_lowerdirs_for_pass).

Subcommands:
  target-layer  print the layer the installer installs by default -- the one
                Limpet content has to land in to reach the installed system
  lowerdirs     overlayfs lowerdir= string for a layer (nearest parent first)
  set-size      rewrite one source's size: in install-sources.yaml
  grub-cfg      rebrand the stock ISO grub.cfg / loopback.cfg menu entries
  md5sum        refresh md5sum.txt lines for files this build replaced
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

import yaml

LAYERED = "fsimage-layered"
STOCK_ENTRY = "Try or Install"


def die(msg: str) -> None:
    print(f"iso_layers: {msg}", file=sys.stderr)
    sys.exit(1)


def load_sources(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not doc.get("sources"):
        die(f"{path} has no sources: list")
    return doc


def target_layer(doc: dict) -> str:
    defaults = [s for s in doc["sources"] if s.get("default")]
    if len(defaults) != 1:
        die(f"expected exactly one default install source, found {len(defaults)}")
    src = defaults[0]
    if src.get("type") != LAYERED:
        die(f"default source {src.get('id')} is type {src.get('type')!r}, not {LAYERED}")
    path = src["path"]
    if not path.endswith(".squashfs"):
        die(f"default source path {path!r} is not a .squashfs layer")
    return path[: -len(".squashfs")]


def parent_layers(layer: str) -> list:
    parts = layer.split(".")
    return [".".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def set_size(doc: dict, layer: str, size: int) -> dict:
    hits = [s for s in doc["sources"] if s.get("path") == f"{layer}.squashfs"]
    if not hits:
        # e.g. LIMPET_ISO_LAYER=minimal.standard.live: live-session only, never installed.
        print(f"iso_layers: {layer}.squashfs is not an install source; sizes unchanged", file=sys.stderr)
    for src in hits:
        src["size"] = size
    return doc


def rebrand_grub(text: str, old: str, new: str) -> str:
    if not re.search(rf'(?m)^\s*menuentry\s+"{STOCK_ENTRY} {re.escape(old)}"', text):
        die(f'no "{STOCK_ENTRY} {old}" menuentry -- base ISO grub.cfg layout changed, re-check before rebranding')

    def title(m):
        return m.group(1) + m.group(2).replace(old, new) + m.group(3)

    return re.sub(r'(?m)^(\s*menuentry\s+")([^"]*)(")', title, text)


def refresh_md5(listing: str, root: Path, rel_paths: list) -> str:
    want = {f"./{p.lstrip('/')}" for p in rel_paths}
    seen = set()
    out = []
    for line in listing.splitlines():
        digest, _, name = line.partition("  ")
        if name in want:
            digest = hashlib.md5((root / name).read_bytes()).hexdigest()
            seen.add(name)
        out.append(f"{digest}  {name}" if name else line)
    for name in sorted(want - seen):
        out.append(f"{hashlib.md5((root / name).read_bytes()).hexdigest()}  {name}")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("target-layer")
    p.add_argument("--sources", type=Path, required=True)

    p = sub.add_parser("lowerdirs")
    p.add_argument("--layer", required=True)
    p.add_argument("--root", required=True, help="dir holding one mountpoint per lower layer")

    p = sub.add_parser("set-size")
    p.add_argument("--sources", type=Path, required=True)
    p.add_argument("--layer", required=True)
    p.add_argument("--size", type=int, required=True)

    p = sub.add_parser("grub-cfg")
    p.add_argument("--file", type=Path, required=True, help="rewritten in place")
    p.add_argument("--old", default="Xubuntu")
    p.add_argument("--new", default="Limpet")

    p = sub.add_parser("md5sum")
    p.add_argument("--root", type=Path, required=True, help="ISO root md5sum.txt paths are relative to")
    p.add_argument("files", nargs="+")

    args = ap.parse_args(argv)
    if args.cmd == "target-layer":
        print(target_layer(load_sources(args.sources)))
    elif args.cmd == "lowerdirs":
        parents = parent_layers(args.layer)
        if not parents:
            die(f"{args.layer} is the root layer; it has no lowerdirs")
        print(":".join(f"{args.root}/{p}" for p in parents))
    elif args.cmd == "set-size":
        doc = set_size(load_sources(args.sources), args.layer, args.size)
        args.sources.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    elif args.cmd == "grub-cfg":
        args.file.write_text(rebrand_grub(args.file.read_text(encoding="utf-8"), args.old, args.new), encoding="utf-8")
    elif args.cmd == "md5sum":
        listing = args.root / "md5sum.txt"
        listing.write_text(refresh_md5(listing.read_text(encoding="utf-8"), args.root, args.files), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
