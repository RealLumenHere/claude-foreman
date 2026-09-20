#!/usr/bin/env python3
"""Fill be3600's lan_ip cell in /home/lumen/FLEET_MAP.md (wq#1800).

storage-manifest-drift reported be3600 lan_ip declared=None live=192.168.0.1.
The roster row carries the placeholder `VERIFY` in the lan_ip column; the store
has held `192.168.0.1 (grade B: apep's default gateway + ARP a8:6e:84:ad:0f:14,
2026-09-10; not read from the router itself)` as an open fact since 2026-09-10.
Corroborated by apep's own row, 192.168.0.161 — apep sits behind this router.

Grade B is carried into the cell verbatim-in-spirit: the value is inferred from a
downstream host, not read off the router, and the map should not claim otherwise.

Runs as lumen (the file's owner) via `lumen-run start -- python3 apply.py`;
FLEET_MAP.md sits at /home/lumen root, outside every worker worktree and
--add-dir root (ISSUE-LOG #322). Idempotent: a second run is a no-op.
"""
import re
import shutil
import sys
import time
from pathlib import Path

MAP = Path("/home/lumen/FLEET_MAP.md")
NODE = "be3600"
OLD_CELL = "VERIFY"
NEW_CELL = "192.168.0.1 (grade B)"
LAN_IP_COL = 6  # | node | status | hardware | role | tailscale_ip | lan_ip | notes |


def patch(line):
    cells = line.split("|")
    if cells[LAN_IP_COL].strip() != OLD_CELL:
        return None
    cells[LAN_IP_COL] = f" {NEW_CELL} "
    return "|".join(cells)


def main():
    lines = MAP.read_text(encoding="utf-8").splitlines(keepends=True)
    rows = [i for i, l in enumerate(lines)
            if re.match(rf"\|\s*{NODE}\b", l, re.IGNORECASE)]
    if len(rows) != 1:
        print(f"ABORT: expected 1 {NODE} roster row, found {len(rows)}", file=sys.stderr)
        return 2
    i = rows[0]
    if NEW_CELL in lines[i]:
        print(f"NOOP: line {i + 1} already carries {NEW_CELL}")
        return 0
    patched = patch(lines[i])
    if patched is None:
        print(f"ABORT: line {i + 1} lan_ip cell is not {OLD_CELL!r}: {lines[i].strip()}",
              file=sys.stderr)
        return 2

    backup = MAP.with_suffix(f".md.bak.{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(MAP, backup)
    lines[i] = patched
    MAP.write_text("".join(lines), encoding="utf-8")
    print(f"BACKUP: {backup}")
    print(f"PATCHED line {i + 1}: {patched.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
