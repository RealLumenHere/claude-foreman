#!/usr/bin/env python3
"""wq#2529: one-time sweep of open tickets for a Liveness: line that uses grep -v/-qv/-vq
(the always-true-never-false bug wq#1975 shipped with). Report-only — prints hits, fixes
nothing; a flagged ticket's own Liveness: line needs a human/agent call on which direction
the probe should actually invert (drop -v for grep -q, or restructure with grep -qL /
a negated `! grep -q`), same as wq#1975's own fix.

    python3 sweep_liveness_grep_invert.py [--db /home/lumen/agents/work-queue/queue.db]
"""
import argparse
import sqlite3
import sys


def liveness_grep_invert_line(text):
    """Same detection as the wq add/edit lint gate (apply_wq_patch.py's
    _liveness_grep_invert_line) — kept as an independent copy here so the sweep never
    depends on the patched CLI being importable."""
    for line in (text or "").splitlines():
        if "Liveness:" not in line:
            continue
        tokens = line.split()
        for i, tok in enumerate(tokens):
            if tok != "grep" and not tok.endswith("/grep"):
                continue
            for nxt in tokens[i + 1:]:
                if nxt in ("|", ";", "&&"):
                    break
                if nxt.startswith("--"):
                    if nxt == "--invert-match":
                        return line
                    continue
                if nxt.startswith("-") and "v" in nxt[1:]:
                    return line
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/home/lumen/agents/work-queue/queue.db")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db)
    rows = conn.execute(
        "SELECT id,status,detail FROM tasks WHERE status IN "
        "('open','blocked','frank_gated','claimed')"
    ).fetchall()
    hits = []
    for tid, status, detail in rows:
        bad = liveness_grep_invert_line(detail)
        if bad:
            hits.append((tid, status, bad.strip()))
    print(f"{len(hits)} open ticket(s) with a grep -v/-qv/-vq Liveness: line")
    for tid, status, line in hits:
        print(f"  wq#{tid} [{status}]: {line}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
