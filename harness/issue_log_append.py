#!/usr/bin/env python3
"""wq#3174: append a row to kb/ISSUE-LOG.md with an atomically allocated row number.

Every worker appends its own row, and each one used to pick "max row + 1" by
reading the file and then writing it with a separate Edit. Two workers that read
before either wrote picked the same number: 15 duplicate row numbers landed on
09-28/09-29 alone (rows 915 ... 1075, wq#3174), each one making every "row N"
citation of it ambiguous.

This helper holds an exclusive flock on the log file itself for the whole
read-max-append step, so concurrent callers serialize and each gets a distinct
number. It only protects callers that go through it: an Edit-tool append takes no
lock, which is why the worker doctrine (harness/prompts/worker.md) now points
here, and why ops/wq2857-issue-log-hygiene-alarm still watches for collisions.

Usage:
  issue_log_append.py --issue "..." --cause "..." --solution "..." --status SOLVED
  issue_log_append.py --next      # print the number the next append would get
Prints the allocated row number on stdout.
"""
import argparse
import fcntl
import os
import re
import sys

LOG_PATH = "/home/lumen/kb/ISSUE-LOG.md"
# Numeric part only: a disambiguation suffix ("887b", reports/
# 2857_policy_issue_log_row_disambiguation.md) never takes a fresh number.
_ROW_RE = re.compile(r"^\|\s*(\d+)[a-z]?\s*\|", re.M)


def next_row(text):
    return max((int(n) for n in _ROW_RE.findall(text)), default=0) + 1


def cell(value):
    """One table cell: a newline or bare pipe would split the row."""
    return re.sub(r"(?<!\\)\|", r"\\|", " ".join(value.split()))


def append(path, issue, cause, solution, status):
    """Allocate the next row number and append the row, under an exclusive lock.
    Returns the number."""
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        text = fh.read()
        num = next_row(text)
        cells = " | ".join(cell(v) for v in (issue, cause, solution, status))
        fh.write(("" if not text or text.endswith("\n") else "\n") + f"| {num} | {cells} |\n")
        fh.flush()
        os.fsync(fh.fileno())
        return num


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--path", default=LOG_PATH)
    ap.add_argument("--next", action="store_true", help="print the next row number, write nothing")
    for name in ("issue", "cause", "solution", "status"):
        ap.add_argument(f"--{name}")
    args = ap.parse_args(argv)

    if args.next:
        with open(args.path, encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            print(next_row(fh.read()))
        return 0
    missing = [n for n in ("issue", "cause", "solution", "status") if not getattr(args, n)]
    if missing:
        ap.error("required: " + ", ".join(f"--{n}" for n in missing))
    print(append(args.path, args.issue, args.cause, args.solution, args.status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
