#!/usr/bin/env python3
"""wq#2817 probe: are kb/ISSUE-LOG.md's cross-row pointers resolvable?

Doctrine tells every worker to read ISSUE-LOG.md before changing anything, so a
row that says "see row N" is a navigation instruction. wq#2763 found row 824
sending readers to row 834 (an unrelated judgment) instead of 835. This probe
generalises that single finding into three checks over EVERY row, so it can fail
on rows the wq#2817 fix never touched:

  DUP       row number defined more than once -> any "row N" citing it is ambiguous
  DANGLING  a "see row N" pointer whose target row number does not exist
  ONE-WAY   a "see row N" correction pointer where row N never mentions the
            citing row back. Correction rows in this log cite their subject
            ("row 824 above closes rotation row ..." -- row 835), so a missing
            back-reference is the signature of a mis-typed target.

Input is rev-pinned by default (`--rev <sha>` reads through `git show`), so a
run is reproducible after the live file moves on.

Usage:
  probe_pointers.py --rev 10e77b2            # baseline: bug live at kb HEAD
  probe_pointers.py --path /home/lumen/kb/ISSUE-LOG.md
Exit 0 when no check fails, 1 otherwise.
"""
import argparse
import re
import subprocess
import sys

KB_DIR = "/home/lumen/kb"
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")
SEE_RE = re.compile(r"see rows?\s+(\d+)")


def load(rev, path):
    if rev:
        out = subprocess.run(
            ["git", "-C", KB_DIR, "show", f"{rev}:ISSUE-LOG.md"],
            check=True, capture_output=True, text=True,
        )
        return out.stdout.splitlines()
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def rows(lines):
    """row number -> list of (1-based line number, full row text)."""
    found = {}
    for n, text in enumerate(lines, 1):
        m = ROW_RE.match(text)
        if m:
            found.setdefault(int(m.group(1)), []).append((n, text))
    return found


def _findings(found):
    """Yield (key, text) for every failure check() would report.

    key is check()'s finding stripped of line numbers, e.g. 'DUP:836:2' or
    'ONEWAY:164:38' -- the stable identity wq#2857's baseline alarm diffs
    on, since a plain append never shifts an existing row's line number in
    this append-only log but the formatted text embeds 'L<n>' regardless.
    """
    for num in sorted(found):
        if len(found[num]) > 1:
            at = ", ".join(f"L{ln}" for ln, _ in found[num])
            yield (
                f"DUP:{num}:{len(found[num])}",
                f"DUP      row {num} defined {len(found[num])}x ({at})",
            )
    for num in sorted(found):
        for line_no, text in found[num]:
            for target in {int(t) for t in SEE_RE.findall(text)}:
                if target not in found:
                    yield (
                        f"DANGLING:{num}:{target}",
                        f"DANGLING row {num} (L{line_no}) -> 'see row {target}': no such row",
                    )
                    continue
                back = re.compile(rf"row {num}\b")
                if not any(back.search(t) for _, t in found[target]):
                    yield (
                        f"ONEWAY:{num}:{target}",
                        f"ONE-WAY  row {num} (L{line_no}) -> 'see row {target}': "
                        f"row {target} never mentions row {num}",
                    )


def check(found):
    return [text for _, text in _findings(found)]


def finding_keys(found):
    """wq#2857: finding identities only, for a baseline diff (see _findings)."""
    return {key for key, _ in _findings(found)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", help="read ISSUE-LOG.md from this kb rev instead of the worktree")
    ap.add_argument("--path", default=f"{KB_DIR}/ISSUE-LOG.md")
    args = ap.parse_args()

    found = rows(load(args.rev, args.path))
    failures = check(found)
    source = f"rev {args.rev}" if args.rev else args.path
    print(f"source: {source}  rows: {len(found)}  pointers checked: "
          f"{sum(len(set(SEE_RE.findall(t))) for v in found.values() for _, t in v)}")
    for f in failures:
        print(f)
    print(f"FAIL ({len(failures)})" if failures else "PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
