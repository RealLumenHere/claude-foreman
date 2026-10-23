#!/usr/bin/env python3
"""Set GRUB_CMDLINE_LINUX in /etc/default/grub idempotently.

wq#4526 stage10 hook: adds console=ttyS0,115200n8 so the kernel prints boot
output to the serial port -- limpet-serial.log was empty in QEMU because
nothing told the kernel to use ttyS0, which blocked serial-based automated
testing (it had to fall back to VNC/QMP screenshots). Rewrites an existing
GRUB_CMDLINE_LINUX line in place or appends one, so re-running the ISO build
does not duplicate keys. The caller still runs `update-grub` inside the
chroot to compile grub.cfg; this only edits the source of truth GRUB reads.
"""
import argparse
import re
import sys

CONSOLE_ARG = "console=ttyS0,115200n8"


def set_cmdline(text: str, value: str) -> str:
    line = f'GRUB_CMDLINE_LINUX="{value}"'
    pat = re.compile(r"(?m)^#?\s*GRUB_CMDLINE_LINUX=.*$")
    if pat.search(text):
        return pat.sub(line, text, count=1)
    sep = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{sep}{line}\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--default", required=True, help="path to /etc/default/grub")
    ap.add_argument("--console", default=CONSOLE_ARG)
    args = ap.parse_args(argv)

    try:
        with open(args.default, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""

    text = set_cmdline(text, args.console)
    with open(args.default, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f'set_grub_cmdline: GRUB_CMDLINE_LINUX="{args.console}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
