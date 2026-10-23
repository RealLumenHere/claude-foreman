#!/usr/bin/env python3
"""apply_docs.py — wq#4537 doc corrections: stop telling agents to write prose
anywhere in claude_memory.md except its preserved hand-prose region.

Exact-string edits, idempotent: an edit whose new text is already present is
skipped; an edit whose old text is missing too means the doc drifted, and the
whole run fails before writing anything.

Usage:
  apply_docs.py            # apply to the live docs
  apply_docs.py --check    # read-only: exit 0 when every edit is applied, else 1
  apply_docs.py --root DIR # resolve /home/lumen/... targets under DIR (tests)
"""
import argparse
import os
import shutil
import sys

PROSE_FILE = "/home/lumen/minerva/context/claude_memory_prose_2026-10-01.md"

EDITS = [
    ("/home/lumen/ORCHESTRATOR-HANDOFF.md",
     "  /home/lumen/claude_memory.md — Auto-generated from DB, DO NOT hand-edit\n",
     "  /home/lumen/claude_memory.md — Regenerated from DB every --core boot;"
     " hand prose ONLY in its bottom hand-prose region (wq#4537)\n"),
    ("/home/lumen/ORCHESTRATOR-HANDOFF.md",
     "Rules: Do NOT add Anthropic memory slots for fleet facts."
     " claude_memory.md is auto-generated.\n",
     "Rules: Do NOT add Anthropic memory slots for fleet facts."
     " claude_memory.md is regenerated from fleet_memory at\n"
     "every --core boot: everything above its <!-- hand-prose:begin --> marker"
     " is overwritten. Prose goes ONLY inside the\n"
     "hand-prose region at the bottom (wq#4537). Prose lost before that fix: "
     + PROSE_FILE + "\n"),
    ("/home/lumen/minerva/context/fleet_tools.yaml",
     '    desc: "Fleet facts + episodic memory. Query state, never trust dated prose;'
     ' record episodes after real work."\n',
     '    desc: "Fleet facts + episodic memory. Query state, never trust dated prose;'
     ' record episodes after real work. Hand prose goes ONLY in the hand-prose region'
     ' at the bottom of /home/lumen/claude_memory.md; the rest is regenerated every'
     ' boot (wq#4537)."\n'),
]


def plan(root):
    """Return ({path: new_text}, [problems], [already-applied descriptions])."""
    texts, problems, done = {}, [], []
    for path, old, new in EDITS:
        real = os.path.join(root, path.lstrip("/")) if root else path
        if real not in texts:
            with open(real, encoding="utf-8") as fh:
                texts[real] = fh.read()
        t = texts[real]
        if new in t:
            done.append("%s: %s" % (path, new.strip()[:60]))
        elif t.count(old) == 1:
            texts[real] = t.replace(old, new)
        else:
            problems.append("%s: anchor found %d times: %r"
                            % (path, t.count(old), old.strip()[:80]))
    return texts, problems, done


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--root", default="")
    args = ap.parse_args(argv)
    texts, problems, done = plan(args.root)
    if problems:
        for p in problems:
            print("MISS " + p)
        return 1
    if args.check:
        if len(done) == len(EDITS):
            return 0
        print("MISS %d of %d doc edits not applied" % (len(EDITS) - len(done), len(EDITS)))
        return 1
    for real, text in texts.items():
        with open(real, encoding="utf-8") as fh:
            if fh.read() == text:
                continue
        tmp = real + ".wq4537.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        shutil.copymode(real, tmp)
        os.replace(tmp, real)
        print("edited " + real)
    return 0


if __name__ == "__main__":
    sys.exit(main())
