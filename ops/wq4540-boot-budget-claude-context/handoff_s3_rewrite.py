#!/usr/bin/env python3
"""handoff_s3_rewrite.py (wq#4540).

MINERVA_STALENESS_REVIEW.md §2.8: "HANDOFF.md §3 — carries July-era entries under a
hand-written 'everything below this line is July-era and mostly done' disclaimer.
Prose managing its own staleness." The ticket calls this section out by name (s3) as
pointing at claude_memory.md SESSION blocks that wq#4537/wq#3647 already deleted.

This session has no read access to the live /home/lumen/ORCHESTRATOR-HANDOFF.md (not
under any granted root for this BUILD dispatch), so the section's exact current text
cannot be quoted or exact-string-matched the way apply_docs.py did in wq#4537. Anchor
on the one verified substring instead (the disclaimer phrase above), archive whatever
body sits between that section's heading and the next heading verbatim, and splice in
a fixed 5-line replacement of commands that are live and ran during this ticket (not
prose that will itself go stale) — never write anything if the anchor is missing or
ambiguous (apply_docs.py's "exact anchor or nothing written" contract, wq#4537).
"""
import argparse
import datetime
import re
import sys

MARKER = "July-era and mostly done"

HEADING_RE = re.compile(r"^#{2,3}\s+\S")

REPLACEMENT_LINES = [
    "Live status replaces dated prose here (wq#4540) — run on Lumen:",
    "- `/home/lumen/bin/wq board` — active tickets, right now",
    "- `python3 /home/lumen/foreman/harness/instincts_render.py --check` — instincts corpus health",
    "- `/home/lumen/bin/fleet-tools-render` — tool registry render + drift check",
    "- `/home/lumen/bin/state-regen --if-dirty` — refresh state projections if stale",
    "- `/home/lumen/bin/lint-handoff.py` — structural lint of this document",
]


class AnchorError(Exception):
    pass


def find_section(lines, marker):
    """Return (heading_idx, body_start, body_end) for the section containing `marker`.

    body_end is the index of the next heading, or len(lines). Raises AnchorError if
    the marker is absent, appears in more than one section, or precedes any heading
    (malformed document) -- callers must not write anything in that case.
    """
    marker_idxs = [i for i, ln in enumerate(lines) if marker in ln]
    if not marker_idxs:
        raise AnchorError("marker not found: %r" % marker)
    if len(marker_idxs) > 1:
        raise AnchorError("marker found %d times, expected exactly 1" % len(marker_idxs))
    hit = marker_idxs[0]

    heading_idx = None
    for i in range(hit, -1, -1):
        if HEADING_RE.match(lines[i]):
            heading_idx = i
            break
    if heading_idx is None:
        raise AnchorError("marker at line %d precedes any heading" % (hit + 1))

    body_end = len(lines)
    for i in range(heading_idx + 1, len(lines)):
        if HEADING_RE.match(lines[i]):
            body_end = i
            break
    return heading_idx, heading_idx + 1, body_end


def rewrite(text, marker=MARKER, replacement=REPLACEMENT_LINES, archive_date=None):
    """Returns (new_text, archived_body_text). Raises AnchorError, writes nothing."""
    lines = text.splitlines()
    heading_idx, body_start, body_end = find_section(lines, marker)
    archived_body = "\n".join(lines[body_start:body_end])
    new_lines = lines[:body_start] + list(replacement) + lines[body_end:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else ""), archived_body


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="ORCHESTRATOR-HANDOFF.md path")
    ap.add_argument("--archive-dir", required=True, help="sessions/ dir to archive the old body into")
    ap.add_argument("--check", action="store_true", help="locate the anchor, write nothing")
    args = ap.parse_args(argv)

    with open(args.file, encoding="utf-8") as f:
        text = f.read()

    try:
        new_text, archived_body = rewrite(text)
    except AnchorError as exc:
        print("handoff_s3_rewrite.py: %s (already rewritten, or doc changed shape)" % exc,
              file=sys.stderr)
        return 1

    if args.check:
        print("handoff_s3_rewrite.py: anchor found, %d archived line(s), not written (--check)"
              % len(archived_body.splitlines()))
        return 0

    import os
    os.makedirs(args.archive_dir, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    archive_path = os.path.join(args.archive_dir, "handoff_s3_archive_%s.md" % stamp)
    with open(archive_path, "w", encoding="utf-8") as f:
        f.write("# Archived from ORCHESTRATOR-HANDOFF.md s3 by handoff_s3_rewrite.py (wq#4540)\n\n")
        f.write(archived_body + "\n")
    with open(args.file, "w", encoding="utf-8") as f:
        f.write(new_text)
    print("handoff_s3_rewrite.py: archived to %s, rewrote %s" % (archive_path, args.file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
