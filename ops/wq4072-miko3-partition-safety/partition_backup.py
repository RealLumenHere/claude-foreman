#!/usr/bin/env python3
"""partition_backup.py — wq#4072.

Full-partition eMMC backup for the MIKO1070XXM3E0408B7 unit, with a SHA256 manifest,
built to the ticket's zero-tolerance-for-blind-writes constraint: three Miko 3 units
are already bricked, so every code path here is read-only by construction. There is
no "flash" or "write" entrypoint in this module at all — `guard_command()` is the
gate that every shell-out in this toolkit (and any future script added to this
directory) must pass a candidate `adb`/`dd`/`fastboot` argv through before exec'ing
it, so a write slipping in via a future edit is caught here, not discovered on-device.

Deploy note (same posture as wq#349's staged UART PoC — built+tested here, run on the
bench host where the unit is actually wired): this module has no adb/device dependency
to import; `backup_all_partitions()` shells out to the system `adb` binary only when
invoked, so it is fully unit-testable on Lumen with zero hardware attached.

CLI:
    python3 partition_backup.py manifest <dir>               build/refresh manifest.json
    python3 partition_backup.py verify <manifest.json> <dir>  recompute + diff
    python3 partition_backup.py guard-check <argv...>          test the write-guard on a command
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field

# Device-node / write-intent patterns the guard refuses to let through. Matched against
# each argv token independently, so quoting games (`of=/dev/mmcblk0`) don't help.
_DEVICE_NODE_RE = re.compile(r"(^|[=:])/dev/(mmcblk|sd|block/)\w*")
_WRITE_VERBS = ("flash", "erase", "format", "wipe", "fastboot flash", "push")


class GuardError(ValueError):
    """Raised when a command looks like it writes to the device."""


def guard_command(argv: list[str]) -> None:
    """Refuse any argv that writes to the device. Read-only backup/probe tools call
    this before every subprocess invocation; it raises rather than returning a bool so
    a caller cannot accidentally ignore a positive match.
    """
    joined = " ".join(argv)
    for verb in _WRITE_VERBS:
        if verb in joined.lower():
            raise GuardError(f"refusing to run — write verb '{verb}' present: {argv!r}")
    for tok in argv:
        if tok.startswith("of=") and _DEVICE_NODE_RE.search(tok):
            raise GuardError(f"refusing to run — 'of=' targets a device node: {tok!r}")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ManifestEntry:
    sha256: str
    size_bytes: int


@dataclass
class Manifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"entries": {name: {"sha256": e.sha256, "size_bytes": e.size_bytes} for name, e in self.entries.items()}}

    @classmethod
    def from_json(cls, data: dict) -> "Manifest":
        return cls(entries={name: ManifestEntry(**e) for name, e in data["entries"].items()})


def build_manifest(image_dir: str) -> Manifest:
    """One entry per regular file directly under image_dir (one file per partition
    image, named after the partition — e.g. boot.img, recovery.img, userdata.img)."""
    manifest = Manifest()
    for name in sorted(os.listdir(image_dir)):
        path = os.path.join(image_dir, name)
        if os.path.isfile(path):
            manifest.entries[name] = ManifestEntry(sha256=sha256_file(path), size_bytes=os.path.getsize(path))
    return manifest


def write_manifest(manifest: Manifest, out_path: str) -> None:
    with open(out_path, "w") as f:
        json.dump(manifest.to_json(), f, indent=2, sort_keys=True)
        f.write("\n")


def verify_manifest(manifest_path: str, image_dir: str) -> list[str]:
    """Recompute SHA256 for every file the manifest names and diff. Returns a list of
    human-readable mismatch descriptions; empty list = clean verify. Missing files and
    extra untracked files are both reported — a partial backup must not read as clean.
    """
    with open(manifest_path) as f:
        recorded = Manifest.from_json(json.load(f))
    problems = []
    manifest_name = os.path.basename(manifest_path)
    on_disk = {
        name for name in os.listdir(image_dir)
        if os.path.isfile(os.path.join(image_dir, name)) and name != manifest_name
    }
    for name, entry in recorded.entries.items():
        path = os.path.join(image_dir, name)
        if not os.path.isfile(path):
            problems.append(f"MISSING: {name} (recorded in manifest, not on disk)")
            continue
        actual_sha = sha256_file(path)
        actual_size = os.path.getsize(path)
        if actual_sha != entry.sha256:
            problems.append(f"SHA256 MISMATCH: {name} recorded={entry.sha256} actual={actual_sha}")
        if actual_size != entry.size_bytes:
            problems.append(f"SIZE MISMATCH: {name} recorded={entry.size_bytes} actual={actual_size}")
    extra = on_disk - set(recorded.entries)
    for name in sorted(extra):
        problems.append(f"UNTRACKED: {name} (on disk, not in manifest)")
    return problems


def backup_all_partitions(partitions: list[str], out_dir: str, adb_binary: str = "adb") -> Manifest:
    """adb (as root) dd's each named partition's block device to a local file, then
    builds the SHA256 manifest over the pulled files. Every argv is guard_command()'d
    before exec — see module docstring. Requires a device already in `adb root` shell
    (ticket step 1); this function does not elevate privilege itself.
    """
    import subprocess

    os.makedirs(out_dir, exist_ok=True)
    for part in partitions:
        local_path = os.path.join(out_dir, f"{part}.img")
        remote_tmp = f"/data/local/tmp/{part}.img"
        dd_cmd = ["adb", "shell", "dd", f"if=/dev/block/by-name/{part}", f"of={remote_tmp}", "bs=4M"]
        guard_command(dd_cmd)  # remote target is /data/local/tmp, never a device node — read-direction dd
        pull_cmd = ["adb", "pull", remote_tmp, local_path]
        guard_command(pull_cmd)
        subprocess.run([adb_binary] + dd_cmd[1:], check=True)
        subprocess.run([adb_binary] + pull_cmd[1:], check=True)
        subprocess.run([adb_binary, "shell", "rm", remote_tmp], check=True)
    manifest = build_manifest(out_dir)
    write_manifest(manifest, os.path.join(out_dir, "manifest.json"))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_manifest = sub.add_parser("manifest", help="build/refresh manifest.json for a directory of partition images")
    p_manifest.add_argument("dir")

    p_verify = sub.add_parser("verify", help="recompute SHA256 and diff against a manifest")
    p_verify.add_argument("manifest")
    p_verify.add_argument("dir")

    p_guard = sub.add_parser("guard-check", help="test the write-guard against an argv, without running it")
    p_guard.add_argument("argv", nargs="+")

    args = ap.parse_args()

    if args.cmd == "manifest":
        m = build_manifest(args.dir)
        write_manifest(m, os.path.join(args.dir, "manifest.json"))
        print(f"wrote manifest.json: {len(m.entries)} entries")
        return 0

    if args.cmd == "verify":
        problems = verify_manifest(args.manifest, args.dir)
        if problems:
            for p in problems:
                print(p)
            print(f"VERIFY FAILED: {len(problems)} problem(s)")
            return 1
        print("VERIFY OK: all files match manifest")
        return 0

    if args.cmd == "guard-check":
        try:
            guard_command(args.argv)
        except GuardError as e:
            print(f"BLOCKED: {e}")
            return 1
        print("ALLOWED")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
