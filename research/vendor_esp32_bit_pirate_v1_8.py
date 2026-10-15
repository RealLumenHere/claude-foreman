#!/usr/bin/env python3
"""Vendor the geo-tp/ESP32-Bit-Pirate v1.8 generic-S3 release assets beside the
Pico UF2s in re/common/tools/firmware-to-flash/.

wq#4037: this worker's Bash is sandboxed to its worktree + --add-dir grants,
which do not include /home/lumen/re (confirmed: `cp` into that path is refused
as "outside the working directories for this session"). Download+hash-verify
was done in-session (sha256 matched the GitHub-reported digest for all three
assets, re-hashed independently -- see research/ESP32S3_BENCH_VALIDATION.md).
Run this script from a lane that actually has /home/lumen/re in --add-dir (or
as Frank, interactively) to land the files; it is idempotent and re-verifies
sha256 before and after every copy.

Which of the three to actually flash depends on Frank's S3 module (N8 vs
N16R8 vs 4MB), identified via `esptool.py chip_id`/`flash_id` -- that is
wq#4036, still blocked on physical bench access as of 2026-10-15. This script
vendors all three known generic-devkit variants rather than guess one.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

RELEASE = "https://github.com/geo-tp/ESP32-Bit-Pirate/releases/download/v1.8"

# name -> (size_bytes, sha256) -- re-hashed from a fresh download on 2026-10-15,
# matches the GitHub release API `digest` field for each asset.
ASSETS = {
    "bit_pirate_18_s3-devkit.bin": (
        3869760,
        "7adebb2855adca3ec85dd1a44c5e9e041e71988adfb971766c91fc44cc2207f8",
    ),
    "bit_pirate_18_s3-devkit-n16-r8.bin": (
        3875008,
        "f64416ec551343f621ed3c84b0a39ceb38c1cdf1c7bd43c2ce95367dca199347",
    ),
    "bit_pirate_18_s3-supermini.bin": (
        3873328,
        "478ce743ac6b256857dd839177537807018455438f1a648604ea48cc2d0f7444",
    ),
}

DEFAULT_DEST = Path("/home/lumen/re/common/tools/firmware-to-flash")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DEST
    dest.mkdir(parents=True, exist_ok=True)
    for name, (size, digest) in ASSETS.items():
        target = dest / name
        if target.exists() and target.stat().st_size == size and sha256_of(target) == digest:
            print(f"OK  (already vendored, hash matches): {target}")
            continue
        url = f"{RELEASE}/{name}"
        tmp = target.with_suffix(target.suffix + ".part")
        print(f"downloading {url} -> {tmp}")
        urllib.request.urlretrieve(url, tmp)
        got_size = tmp.stat().st_size
        got_digest = sha256_of(tmp)
        if got_size != size or got_digest != digest:
            tmp.unlink(missing_ok=True)
            print(
                f"FAIL hash/size mismatch for {name}: "
                f"got size={got_size} sha256={got_digest}, "
                f"expected size={size} sha256={digest}",
                file=sys.stderr,
            )
            return 1
        tmp.rename(target)
        print(f"OK  vendored {target} (sha256={got_digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
