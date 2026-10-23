"""wq#4604 stage 13 helper: the pure logic behind Limpet ISO torrent distribution --
magnet-link derivation, command construction for the privileged steps
(package install, GPG sign, detached seed unit), and the GitHub release notes
body. Kept separate from distribute.sh so it is unit-testable without root,
gpg, or a real transmission install (ISSUE-LOG doctrine: verify, don't guess --
the magnet link is derived from the BitTorrent info-hash per BEP 3 rather than
trusting a specific transmission-show CLI flag across versions).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.parse

TRACKERS_DEFAULT = ["udp://tracker.opentrackr.org:1337/announce"]


def _bdecode_with_spans(data: bytes, i: int):
    """Decode one bencoded value starting at data[i]. Returns (value, end_index).
    For dicts, value is (dict, spans) where spans maps each key to the
    (start, end) byte offsets of its *raw* encoded value -- needed to hash the
    "info" value's exact original bytes rather than a re-encoding of it."""
    marker = data[i:i + 1]
    if marker == b"i":
        j = data.index(b"e", i + 1)
        return int(data[i + 1:j]), j + 1
    if marker == b"l":
        i += 1
        items = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode_with_spans(data, i)
            items.append(v)
        return items, i + 1
    if marker == b"d":
        i += 1
        d = {}
        spans = {}
        while data[i:i + 1] != b"e":
            key, i = _bdecode_with_spans(data, i)
            start = i
            val, i = _bdecode_with_spans(data, i)
            d[key] = val
            spans[key] = (start, i)
        return (d, spans), i + 1
    if marker.isdigit():
        j = data.index(b":", i)
        length = int(data[i:j])
        j += 1
        return data[j:j + length], j + length
    raise ValueError(f"not bencode at offset {i}: {data[i:i + 16]!r}")


def info_hash(torrent_bytes: bytes) -> str:
    """BEP 3 info-hash: sha1 of the raw bytes of the top-level 'info' value."""
    (top, spans), _end = _bdecode_with_spans(torrent_bytes, 0)
    if b"info" not in top:
        raise ValueError("torrent file has no top-level 'info' dict")
    start, end = spans[b"info"]
    return hashlib.sha1(torrent_bytes[start:end]).hexdigest()


def magnet_link(info_hash_hex: str, display_name: str, trackers: list[str]) -> str:
    # xt is plain ASCII (urn:btih:<hex>) and conventionally left unescaped;
    # dn/tr carry arbitrary text/URLs and must be percent-encoded.
    parts = [f"xt=urn:btih:{info_hash_hex}", f"dn={urllib.parse.quote(display_name, safe='')}"]
    parts += [f"tr={urllib.parse.quote(t, safe='')}" for t in trackers]
    return "magnet:?" + "&".join(parts)


def transmission_create_cmd(
    iso_path: str, torrent_out: str, trackers: list[str], comment: str
) -> list[str]:
    cmd = ["transmission-create", "-o", torrent_out, "-c", comment]
    for t in trackers:
        cmd += ["-t", t]
    cmd.append(iso_path)
    return cmd


def apt_install_cmd() -> list[str]:
    # sudo -n: Lumen's passwordless-sudo posture (dispatch_recover.py's
    # "sudo -n systemd-run" convention) -- fails loud rather than hanging on a
    # password prompt if that posture ever changes.
    return ["sudo", "-n", "apt-get", "install", "-y", "transmission-cli"]


def seed_unit_cmd(unit_name: str, seed_dir: str, torrent_path: str) -> list[str]:
    # Detached transient unit, never a foreground/unattended process
    # (instinct 5's "never foreground, never unattended" posture for
    # long-running daemons on Lumen).
    return [
        "sudo", "-n", "systemd-run",
        f"--unit={unit_name}",
        "--collect",
        "--description=Limpet ISO torrent seed",
        "transmission-cli", "-w", seed_dir, torrent_path,
    ]


def gpg_sign_cmd(key_id: str, infile: str, outfile: str) -> list[str]:
    return [
        "gpg", "--batch", "--yes",
        "--local-user", key_id,
        "--detach-sign", "--output", outfile, infile,
    ]


def release_notes(
    version: str,
    iso_name: str,
    magnet: str,
    trackers: list[str],
    key_id: str,
) -> str:
    tracker_lines = "\n".join(f"- `{t}`" for t in trackers)
    return f"""# Limpet {version}

## Download

| Asset | Purpose |
|---|---|
| `{iso_name}` | the installable ISO |
| `{iso_name}.torrent` | torrent metainfo (trackers below + DHT) |
| `SHA256SUMS`, `SHA512SUMS` | checksums -- verify before writing to USB |
| `SHA256SUMS.gpg` | detached signature over `SHA256SUMS`, key `{key_id}` |

## Magnet link

```
{magnet}
```

## Trackers

{tracker_lines}

Lumen seeds this release directly; the magnet/torrent also works once any
other peer has a complete copy.

## Verify + flash

See `docs/limpet-download-verify-flash.md` for the full walkthrough.

---
Publish with (run by whoever holds `gh release` write access to the public
repo -- not run by this build worker, see distribute.sh stage_release):

```
gh release create v{version} \\
  {iso_name} {iso_name}.torrent SHA256SUMS SHA512SUMS SHA256SUMS.gpg \\
  --title "Limpet {version}" --notes-file RELEASE_NOTES.md
```
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_magnet = sub.add_parser("magnet", help="print the magnet link for a .torrent file")
    p_magnet.add_argument("--torrent", required=True)
    p_magnet.add_argument("--name", required=True)
    p_magnet.add_argument("--tracker", action="append", dest="trackers", default=None)

    p_notes = sub.add_parser("notes", help="print GitHub release notes markdown")
    p_notes.add_argument("--version", required=True)
    p_notes.add_argument("--iso-name", required=True)
    p_notes.add_argument("--magnet", required=True)
    p_notes.add_argument("--key-id", required=True)
    p_notes.add_argument("--tracker", action="append", dest="trackers", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "magnet":
        with open(args.torrent, "rb") as fh:
            data = fh.read()
        trackers = args.trackers or TRACKERS_DEFAULT
        print(magnet_link(info_hash(data), args.name, trackers))
        return 0

    if args.cmd == "notes":
        trackers = args.trackers or TRACKERS_DEFAULT
        print(
            release_notes(args.version, args.iso_name, args.magnet, trackers, args.key_id),
            end="",
        )
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
