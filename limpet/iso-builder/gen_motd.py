"""wq#3946 stage 9 helper: render /etc/motd from the terminal mark and color
tokens fixed in docs/branding-spec.md section 1.3 / design/limpet/brand/tokens.json.

The ASCII mark itself is a committed design asset (branding-spec.md:70-76); this
module only applies the ansi16_fallback token colors the spec requires ("terminals
without truecolor... degrades to the user's own 16-color scheme", branding-spec.md
section 2.4) rather than hand-coding escape codes next to the art.
"""
from __future__ import annotations

import argparse
import json
import pathlib

DEFAULT_TOKENS = pathlib.Path(__file__).parent.parent.parent / "design/limpet/brand/tokens.json"

_ANSI16 = {
    "black": 30, "red": 31, "green": 32, "yellow": 33,
    "blue": 34, "magenta": 35, "cyan": 36, "white": 37,
    "bright_black": 90, "bright_red": 91, "bright_green": 92,
    "bright_yellow": 93, "bright_blue": 94, "bright_magenta": 95,
    "bright_cyan": 96, "bright_white": 97,
}

_MARK_LINES = [
    "      .",
    "     /:\\        l i m p e t",
    "    / : \\       universal RE appliance",
    "   /  :  \\",
    "  '~~~~~~~'",
]

RESET = "\033[0m"


def _fg(role: str, fallback: dict[str, str]) -> str:
    return f"\033[{_ANSI16[fallback[role]]}m"


def render(fallback: dict[str, str]) -> str:
    primary = _fg("primary", fallback)
    secondary = _fg("secondary", fallback)
    muted = _fg("muted", fallback)
    lines = []
    for i, line in enumerate(_MARK_LINES):
        if i == len(_MARK_LINES) - 1:
            lines.append(f"{muted}{line}{RESET}")
            continue
        colored = line.replace(":", f"{RESET}{primary}:{RESET}{secondary}")
        lines.append(f"{secondary}{colored}{RESET}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokens", type=pathlib.Path, default=DEFAULT_TOKENS)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args()
    data = json.loads(args.tokens.read_text())
    text = render(data["ansi16_fallback"])
    if args.out:
        args.out.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
