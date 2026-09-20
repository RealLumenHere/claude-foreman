#!/usr/bin/env python3
"""Print numbered lines of a file around each match. wq#2140: the shell `grep`
is path-scoped to the worker's --add-dir roots, and the prior art this RCA must
read (claude_memory.md) lives outside them.

  memgrep.py <file> <pattern> [context-lines]
"""
import re
import sys


def main():
    path, pattern = sys.argv[1], sys.argv[2]
    ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    rx = re.compile(pattern, re.I)
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    shown = set()
    for i, line in enumerate(lines):
        if rx.search(line):
            shown.update(range(max(0, i - ctx), min(len(lines), i + ctx + 1)))
    for i in sorted(shown):
        print(f"{i + 1}: {lines[i]}")


if __name__ == "__main__":
    main()
