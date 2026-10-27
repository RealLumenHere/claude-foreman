"""List wq4591-a2 project-dir sessions and count 'bypass the intent' hits per file (masked)."""
import glob
import json
import os
import sys

sys.path.insert(0, "/home/lumen/foreman/harness")
import masking  # noqa: E402

base = os.path.expanduser("~/.claude/projects/-home-lumen-worktrees-wq4591-a2")
for p in sorted(glob.glob(base + "/*.jsonl")):
    n = 0
    hits = []
    for lineno, line in enumerate(open(p, encoding="utf-8"), 1):
        n += 1
        if "bypass the intent" in line.lower():
            hits.append(lineno)
    print(os.path.basename(p), "lines=%d" % n, "boilerplate_hits=%s" % hits[:10])
