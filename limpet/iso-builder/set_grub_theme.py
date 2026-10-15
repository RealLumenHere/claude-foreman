#!/usr/bin/env python3
"""Set GRUB_THEME / GRUB_GFXMODE in /etc/default/grub idempotently.

wq#3948 stage9 hook: points GRUB at the Limpet graphical theme
(design/limpet/brand/themes/grub/theme.txt, installed to
/boot/grub/themes/limpet/). Rewrites an existing KEY= line in place or appends
one, so re-running the ISO build does not duplicate keys. The caller still runs
`update-grub` inside the chroot to compile grub.cfg; this only edits the source
of truth GRUB reads.
"""
import argparse
import re
import sys

KEYS = {"GRUB_THEME": None, "GRUB_GFXMODE": "1024x768"}


def set_key(text: str, key: str, value: str) -> str:
    line = f'{key}="{value}"'
    pat = re.compile(rf"(?m)^#?\s*{re.escape(key)}=.*$")
    if pat.search(text):
        return pat.sub(line, text, count=1)
    sep = "" if text.endswith("\n") or not text else "\n"
    return f"{text}{sep}{line}\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--default", required=True, help="path to /etc/default/grub")
    ap.add_argument("--theme", required=True, help="GRUB_THEME value (theme.txt path)")
    ap.add_argument("--gfxmode", default=KEYS["GRUB_GFXMODE"])
    args = ap.parse_args(argv)

    try:
        with open(args.default, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""

    text = set_key(text, "GRUB_THEME", args.theme)
    text = set_key(text, "GRUB_GFXMODE", args.gfxmode)
    with open(args.default, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"set_grub_theme: GRUB_THEME={args.theme} GRUB_GFXMODE={args.gfxmode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
