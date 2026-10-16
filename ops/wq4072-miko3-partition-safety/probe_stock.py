#!/usr/bin/env python3
"""probe_stock.py — wq#4072.

Read-only `fastboot getvar` sweep ("FB params" in the ticket) for MIKO1070XXM3E0408B7,
step 2 of the pre-flash sequence. Parses `fastboot getvar all` output into a dict so the
safety map (scratch/research/miko3-partition-safety-map.md) can diff a probed unit's
partition table/slot layout against the benwilson/miko3-jailbreak MT8168 reference
before anything is written. Shares partition_backup.guard_command() so a future edit
that slips a `fastboot flash`/`erase`/`oem` write subcommand in here is still caught.

CLI:
    python3 probe_stock.py run [--fastboot-binary fastboot]   probe an attached device
    python3 probe_stock.py parse <getvar-output.txt>           parse a captured log
"""
from __future__ import annotations

import argparse
import re
import sys

from partition_backup import GuardError, guard_command

_GETVAR_LINE_RE = re.compile(r"^\(bootloader\)\s*([\w.\-:]+):\s*(.*)$")


def parse_getvar_output(text: str) -> dict[str, str]:
    params = {}
    for line in text.splitlines():
        m = _GETVAR_LINE_RE.match(line.strip())
        if m:
            params[m.group(1)] = m.group(2).strip()
    return params


def run_probe(fastboot_binary: str = "fastboot") -> dict[str, str]:
    import subprocess

    cmd = [fastboot_binary, "getvar", "all"]
    guard_command(cmd)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    # fastboot writes getvar output to stderr, not stdout
    return parse_getvar_output(result.stderr + result.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="probe an attached device over fastboot")
    p_run.add_argument("--fastboot-binary", default="fastboot")

    p_parse = sub.add_parser("parse", help="parse a captured `fastboot getvar all` log file")
    p_parse.add_argument("logfile")

    args = ap.parse_args()

    try:
        if args.cmd == "run":
            params = run_probe(args.fastboot_binary)
        else:
            with open(args.logfile) as f:
                params = parse_getvar_output(f.read())
    except GuardError as e:
        print(f"BLOCKED: {e}")
        return 1

    for k, v in sorted(params.items()):
        print(f"{k} = {v}")
    print(f"\n{len(params)} param(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
