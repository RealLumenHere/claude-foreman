"""wq#3971 -- LIMPET read-only live operator console (wq#3944/A8, gap G5).

A follow-mode reader of a workspace's per-call *evidence log* -- the append-only
``evidence/tool-calls.jsonl`` that the meta-server writes, one JSON evidence block
per ``tools/call`` (wq#3964, ``ops/wq3808-limpet-mcp-meta-server-a1/evidence.py``).
Limpet already keeps a replayable git trail but had no live view of what the agent
is doing on the box (design/wq3929_competitive_analysis.md:125-127); this is the
cheap version of that view: ``tail -f`` over the evidence log.

Each block is rendered as one line -- ``ts  tool  ok  coverage`` -- so an operator
can watch calls scroll by, and ``--json`` emits one projected object per line
(JSONL) for the agent-chat GUI pane (design/wq3903) to append to. Text and JSON are
both derived from one ``project()`` of the block, so the two views cannot disagree
(the same posture workspace_report.py takes across md/html/json).

READ-ONLY by construction: this module only ever opens the log for reading. It
exposes no control action and never writes to the workspace. Coverage notes are the
one free-text field a child tool controls, so they are credential-masked before
print (fleet instinct 4), reusing workspace_report's masker so the packaged tool
masks identically everywhere.
"""
from __future__ import annotations

import json
import os
import sys
import time

import workspace_report as wr

# The on-disk contract with wq#3964's EvidenceLog. Kept as a literal (not imported
# from the meta-server tree, which is a separate lane's worktree) so the console
# depends only on the log's location, not on that module.
EVIDENCE_LOG_RELPATH = os.path.join("evidence", "tool-calls.jsonl")

DEFAULT_POLL_INTERVAL = 0.5


def log_path(workspace: str) -> str:
    return os.path.join(workspace, EVIDENCE_LOG_RELPATH)


def _block_ts(block: dict) -> str:
    """When the call happened: finish time if known, else start, else a bare ts."""
    for key in ("finished_at", "started_at", "ts"):
        val = block.get(key)
        if val:
            return str(val)
    return "-"


def project(block: dict) -> dict:
    """The console's view of one evidence block: the fields a live line needs.

    ``coverage`` is masked here because it is child-supplied free text that can
    quote target output; everything else is identity/status the meta-server set.
    """
    coverage = block.get("coverage")
    if isinstance(coverage, str):
        coverage = wr.mask_text(coverage)
    return {
        "ts": _block_ts(block),
        "tool": block.get("tool") or block.get("operation") or "-",
        "ok": bool(block.get("ok")),
        "coverage": coverage,
        "evidence_id": block.get("evidence_id"),
        "untrusted_content": bool(block.get("untrusted_content")),
    }


def format_line(proj: dict) -> str:
    """One tab-separated text line: ts, tool, ok, coverage (CLI convention)."""
    ok = "ok" if proj["ok"] else "FAIL"
    cov = proj["coverage"]
    cov = "-" if cov in (None, "") else str(cov)
    return "\t".join([proj["ts"], str(proj["tool"]), ok, cov])


def render_one(block: dict, *, json_mode: bool) -> str:
    """Render a single evidence block to its text or JSON output line."""
    proj = project(block)
    if json_mode:
        return json.dumps(proj, sort_keys=True)
    return format_line(proj)


def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None  # a torn/partial append: skip, the full line arrives next read
    return obj if isinstance(obj, dict) else None


def read_blocks(workspace: str) -> list[dict]:
    """Every evidence block currently in the log, in append (chronological) order.

    Malformed or blank lines are skipped rather than aborting the read, so one bad
    append never blinds the console to the rest of the log.
    """
    path = log_path(workspace)
    if not os.path.exists(path):
        return []
    blocks = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            block = _parse_line(line)
            if block is not None:
                blocks.append(block)
    return blocks


def render_lines(blocks: list[dict], *, json_mode: bool = False) -> list[str]:
    """Render blocks to output lines, preserving order. Pure; used by tests."""
    return [render_one(b, json_mode=json_mode) for b in blocks]


def emit(workspace: str, *, json_mode: bool = False, out=None) -> int:
    """Print the whole log once and return the number of blocks emitted."""
    out = sys.stdout if out is None else out
    blocks = read_blocks(workspace)
    for line in render_lines(blocks, json_mode=json_mode):
        print(line, file=out, flush=True)
    return len(blocks)


def follow(workspace: str, *, json_mode: bool = False, out=None,
           poll_interval: float = DEFAULT_POLL_INTERVAL, stop=None) -> int:
    """Stream the log and keep emitting blocks as they are appended (``tail -f``).

    Reads from a tracked byte offset so each block prints exactly once, holding a
    partial trailing line back until its newline arrives (the EvidenceLog appends one
    whole record per ``write``, but a reader can still catch a write mid-flight).
    Polls every ``poll_interval`` seconds. ``stop`` is an optional predicate checked
    once per poll; when it returns true the loop ends -- the test seam that keeps a
    follow run bounded. Returns the number of blocks emitted.
    """
    out = sys.stdout if out is None else out
    path = log_path(workspace)
    offset = 0
    pending = ""
    emitted = 0
    while True:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                fh.seek(offset)
                chunk = fh.read()
                offset = fh.tell()
            pending += chunk
            lines = pending.split("\n")
            pending = lines.pop()  # trailing fragment (empty if chunk ended on \n)
            for line in lines:
                block = _parse_line(line)
                if block is not None:
                    print(render_one(block, json_mode=json_mode), file=out, flush=True)
                    emitted += 1
        if stop is not None and stop():
            return emitted
        time.sleep(poll_interval)
