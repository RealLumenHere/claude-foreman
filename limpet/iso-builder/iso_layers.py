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
  grub-cfg      rebrand the stock ISO grub.cfg / loopback.cfg menu entries and
                add the serial console kernel args (wq#4534)
  md5sum        refresh md5sum.txt lines for files this build replaced
  merge-dpkg-status      three-way merge of var/lib/dpkg/status for the live layer (wq#4558)
  merge-extended-states  same for var/lib/apt/extended_states
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

import yaml

LAYERED = "fsimage-layered"
STOCK_ENTRY = "Try or Install"
# wq#4534: tty0 first, ttyS0 last -- the kernel's last console= arg becomes
# /dev/console, so this keeps the local display live while making ttyS0
# primary for stage12's QEMU "-serial file:..." boot test.
SERIAL_ARGS = "console=tty0 console=ttyS0,115200n8"
# wq#4564: console=ttyS0 alone still shipped a near-empty serial log --
# "quiet" lowers console_loglevel fleet-wide (every console, ttyS0 included),
# so most of what the kernel/systemd would otherwise print never gets
# written. "splash" is left alone: Plymouth only takes over the tty0
# framebuffer, it doesn't touch what printk sends to ttyS0, so dropping
# "quiet" instead of "splash" keeps the wq#3948 graphical boot branding on
# physical/local boots while unblocking the serial log. loglevel=7 is placed
# after the dropped "quiet" (cmdline tokens are parsed in order, last one
# wins) and systemd.show_status=1 forces unit status lines too, since
# systemd separately honors "quiet" straight off /proc/cmdline.
VERBOSE_ARGS = "loglevel=7 systemd.show_status=1"


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


# wq#4526 fixed console=ttyS0 on the installed system's grub (chroot
# /etc/default/grub via set_grub_cmdline.py). The live ISO's own boot menu --
# this file -- ships kernel args baked into grub.cfg/loopback.cfg, copied
# verbatim from the base ISO (build.sh stage11), so stage12's QEMU boot test
# (which boots the ISO, not an installed system) still got an empty serial
# log. Matches on the "linux ... --- quiet splash" shape both files ship:
# console args land right before "---", and (wq#4564) the verbosity args
# land in the "quiet splash" tail itself, replacing "quiet". A layout change
# either side of "---" is caught by the die() below instead of silently
# landing args in the wrong half of the line.
LINUX_LINE = re.compile(r'(?m)^(\s*linux\s+\S+(?:\s+\S+)*?)(\s*---)(.*)$')


def add_serial_console(text: str, args: str = SERIAL_ARGS, verbose_args: str = VERBOSE_ARGS) -> str:
    if not LINUX_LINE.search(text):
        die('no "linux ... ---" kernel line -- base ISO grub.cfg kernel-arg layout '
            "changed, re-check before adding serial console args")

    def inject(m):
        pre, sep, tail = m.group(1), m.group(2), m.group(3)
        if "console=ttyS0" not in pre:
            pre = f"{pre} {args}"
        if "loglevel=" not in tail:
            tail = re.sub(r"\bquiet\b\s*", "", tail, count=1)
            tail = f"{tail} {verbose_args}"
        return f"{pre}{sep}{tail}"

    return LINUX_LINE.sub(inject, text)


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


# --- wq#4558 / wq#4557: dpkg database of the live layer -----------------------
# The live layer (minimal.standard.live) ships its own FULL var/lib/dpkg/status and
# sits on top of the overlay, so it shadows the target layer's copy: packages the
# build adds look uninstalled and packages it removes look installed (measured on
# limpet-20261021.iso: 0/11 added visible, 91/91 removed still "installed"). The
# fix is a three-way merge of (base target-layer status, live status, modified
# target-layer status). reports/wq4557_live_layer_dpkg_consistency.md section 6.

# Fields livecd-rootfs rewrites in the live layer without changing the package.
META_FIELDS = frozenset({"Priority", "Section", "Status", "Config-Version"})


class MergeConflict(Exception):
    """The three inputs disagree in a way a merge must not paper over."""


def parse_stanzas(text: str) -> dict:
    """Map (Package, Architecture) -> stanza text (no trailing blank lines)."""
    out = {}
    for block in re.split(r"\n\s*\n", text.strip("\n")):
        block = block.strip("\n")
        if not block:
            continue
        pkg = re.search(r"^Package: (.+)$", block, re.M)
        if not pkg:
            raise MergeConflict(f"stanza without a Package field: {block[:60]!r}")
        arch = re.search(r"^Architecture: (.+)$", block, re.M)
        key = (pkg.group(1).strip(), arch.group(1).strip() if arch else "")
        if key in out:
            raise MergeConflict(f"duplicate stanza for {key[0]}:{key[1]}")
        out[key] = block
    return out


def _fields(block: str) -> dict:
    fields, cur = {}, None
    for line in block.splitlines():
        if line[:1] in (" ", "\t") and cur:
            fields[cur] += "\n" + line
        else:
            cur, _, val = line.partition(":")
            fields[cur] = val.strip()
    return fields


def _substance(block: str, meta=META_FIELDS) -> dict:
    return {k: v for k, v in _fields(block).items() if k not in meta}


def _dep_groups(block: str):
    f = _fields(block)
    for fld in ("Depends", "Pre-Depends"):
        for group in f.get(fld, "").replace("\n", " ").split(","):
            alts = [re.sub(r"[\s(:].*", "", a.strip()) for a in group.split("|") if a.strip()]
            if alts:
                yield fld, alts


def _three_way(base: dict, live: dict, mod: dict, meta=META_FIELDS):
    """Return (kept stanzas, removed package names)."""
    keep, removed = {}, set()
    for key in sorted(set(base) | set(live) | set(mod)):
        b, l, m = base.get(key), live.get(key), mod.get(key)
        if m is not None and b is None:
            # added by the build
            if l is not None and _substance(l, meta) != _substance(m, meta):
                raise MergeConflict(f"{key[0]}: added by the build but the live layer carries a different stanza")
            keep[key] = m
        elif b is not None and m is None:
            removed.add(key[0])  # removed by the build: drop even if the live layer lists it
        elif b is not None and m is not None and m == b:
            if l is not None:
                keep[key] = l  # untouched by the build: the live layer's metadata wins
        elif b is not None and m is not None:
            if l is not None and _substance(l, meta) != _substance(b, meta):
                raise MergeConflict(f"{key[0]}: changed by the build and by the live layer")
            keep[key] = m  # touched by the build (e.g. a version bump)
        else:
            keep[key] = l  # live-only
    return keep, removed


def _render(stanzas: dict) -> str:
    return "\n\n".join(stanzas[k] for k in sorted(stanzas)) + "\n"


def merge_dpkg_status(base: str, live: str, modified: str) -> str:
    b, l, m = parse_stanzas(base), parse_stanzas(live), parse_stanzas(modified)
    keep, removed = _three_way(b, l, m)
    # Only stanzas that exist solely in the live layer are checked: a dependency on a
    # package the build removed means the removal would break them. (Kept parent
    # stanzas already reflect the build's own removal.)
    for key in sorted(set(l) - set(b) - set(m)):
        for fld, alts in _dep_groups(l[key]):
            if all(a in removed for a in alts):
                raise MergeConflict(f"{key[0]}: {fld} {' | '.join(alts)} is only satisfiable by a package the build removed")
    return _render(keep)


def merge_extended_states(base: str, live: str, modified: str, status: str) -> str:
    """apt auto-install marks: same three-way, no metadata fields, and no mark for a
    package the merged dpkg status does not list."""
    keep, _ = _three_way(parse_stanzas(base), parse_stanzas(live), parse_stanzas(modified), meta=frozenset())
    present = {k[0] for k in parse_stanzas(status)}
    keep = {k: v for k, v in keep.items() if k[0] in present}
    return _render(keep) if keep else ""


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

    for name in ("merge-dpkg-status", "merge-extended-states"):
        p = sub.add_parser(name)
        p.add_argument("--base", type=Path, required=True, help="original target-layer file")
        p.add_argument("--live", type=Path, required=True, help="original live-layer file")
        p.add_argument("--modified", type=Path, required=True, help="target-layer file after the build")
        p.add_argument("--out", type=Path, required=True)
        if name == "merge-extended-states":
            p.add_argument("--status", type=Path, required=True, help="merged dpkg status")

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
        text = rebrand_grub(args.file.read_text(encoding="utf-8"), args.old, args.new)
        args.file.write_text(add_serial_console(text), encoding="utf-8")
    elif args.cmd in ("merge-dpkg-status", "merge-extended-states"):
        read = lambda f: f.read_text(encoding="utf-8") if f.exists() else ""
        try:
            if args.cmd == "merge-dpkg-status":
                out = merge_dpkg_status(read(args.base), read(args.live), read(args.modified))
            else:
                out = merge_extended_states(read(args.base), read(args.live), read(args.modified), read(args.status))
        except MergeConflict as exc:
            die(f"{args.cmd}: {exc}")
        args.out.write_text(out, encoding="utf-8")
        print(f"iso_layers: {args.cmd}: wrote {len(parse_stanzas(out))} stanzas to {args.out}", file=sys.stderr)
    elif args.cmd == "md5sum":
        listing = args.root / "md5sum.txt"
        listing.write_text(refresh_md5(listing.read_text(encoding="utf-8"), args.root, args.files), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
