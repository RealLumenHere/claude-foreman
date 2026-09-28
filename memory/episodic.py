#!/usr/bin/env python3
"""episodic.py — the EPISODIC half of fleet agent memory (wq#1183, rewired wq#1635).

Memory splits into an append-only EPISODIC log (this module) and a durable
archive. The wq#1183 pattern-C distiller (distill.py + recall.py + an LLM
extraction step into /home/lumen/fleet-memory) was never built out — no unit
ran it, so worker lessons reached no store any session reads. wq#1635 replaced
it: at attempt end fworker calls commit_to_archive(), which extracts a
deterministic digest (no LLM) from the episode events and commits it to
archival_notes via minerva's lumen_episode.py (memoryd first, so trust derives
from the lane claim in $WQ_AGENT; direct audited write as fallback). The old
distiller pieces live in memory/attic/.

One episode file per ticket run, alongside the other run artifacts fworker
already writes:

    RUNS_ROOT/wq<id>/episode.jsonl      one JSON object per line, append-only

Events carry their attempt number, so a multi-attempt run is one episode with
several attempt_end events. Nothing here ever truncates or rewrites — the log is
the raw record, replayable and re-distillable with a better prompt later.

Stdlib-only (repo convention). Writers must be fail-open: fworker wraps every
call in try/except, so raising here can never fail an attempt.
"""

import glob
import json
import os
import re
import subprocess
import sys
import time

DEFAULT_RUNS_ROOT = os.environ.get(
    "FOREMAN_RUNS_ROOT", os.path.join("/home/lumen/foreman", "_runs"))

EPISODE_FILENAME = "episode.jsonl"


def episode_path(ticket_id, runs_root=None):
    root = runs_root or DEFAULT_RUNS_ROOT
    return os.path.join(root, f"wq{ticket_id}", EPISODE_FILENAME)


def append_event(ticket_id, event, runs_root=None):
    """Append one event to the ticket's episode log. Returns the log path.

    Adds a `ts` (unix epoch) if the caller didn't. Append-only by construction:
    the file is only ever opened in "a" mode.
    """
    if not isinstance(event, dict):
        raise TypeError("event must be a dict")
    path = episode_path(ticket_id, runs_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = dict(event)
    record.setdefault("ts", time.time())
    record.setdefault("ticket", int(ticket_id))
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return path


def read_events(ticket_id, runs_root=None):
    """All events for a ticket, in append order. Malformed lines are skipped
    (a crashed writer must not make the whole episode unreadable)."""
    path = episode_path(ticket_id, runs_root)
    events = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    events.append(ev)
    except OSError:
        return []
    return events


# ---------------------------------------------------------------------------
# wq#1635: episode -> archival_notes, deterministically (no LLM)
# ---------------------------------------------------------------------------

DEFAULT_LUMEN_EPISODE_BIN = "/home/lumen/minerva/lumen_episode.py"
LUMEN_EPISODE_BIN = os.environ.get(
    "LUMEN_EPISODE_BIN", DEFAULT_LUMEN_EPISODE_BIN)


def episode_bin_path():
    """wq#2901: resolved per call, not per import. fworker imports this module once
    and lives a whole ticket, and the test suite sets LUMEN_EPISODE_BIN from an
    autouse fixture that runs long after import — a module constant would ignore it
    and keep writing to the live store."""
    return os.environ.get("LUMEN_EPISODE_BIN") or DEFAULT_LUMEN_EPISODE_BIN

_SUCCESS_CLASSIFICATIONS = ("SUCCESS", "DEGRADED_SUCCESS")
# PRs land on Gitea (/owner/repo/pulls/N) or GitHub (/owner/repo/pull/N).
_PR_URL_RE = re.compile(r"https?://[^\s)>\]]+/pulls?/\d+")

# lumen_episode.py's CLI splits --changed/--failed/--tags on ',' and ';', so
# list items must not contain either or they get re-split into fragments.
def _cli_item(text):
    return re.sub(r"[,;]+", " ", str(text)).strip()


def digest_events(ticket_id, events, runs_root=None):
    """Deterministic digest of a ticket's attempt_end events — the extraction
    step wq#1183 wanted an LLM for, done mechanically: summary/changed/failed/
    why are lifted straight from fields fworker already recorded. Returns None
    when there is nothing to commit (no attempt_end yet)."""
    attempts = [e for e in events if e.get("type") == "attempt_end"]
    if not attempts:
        return None
    last = attempts[-1]
    title = (last.get("title") or "").strip()
    classification = last.get("classification") or "UNKNOWN"
    summary = f"wq#{ticket_id} attempt {last.get('attempt')} {classification}"
    if title:
        summary += f": {title}"

    result_text = last.get("result_text") or ""
    changed = [_cli_item(u) for u in dict.fromkeys(_PR_URL_RE.findall(result_text))]

    failed = []
    for a in attempts:
        c = a.get("classification") or "UNKNOWN"
        if c in _SUCCESS_CLASSIFICATIONS:
            continue
        item = f"attempt {a.get('attempt')}: {c}"
        if a.get("killed_reason"):
            item += f" ({a['killed_reason']})"
        failed.append(_cli_item(item))

    why = result_text.strip()
    if not why:
        tail = last.get("assistant_tail") or []
        why = tail[-1].strip() if tail else ""
    why = " ".join(why.split())[:600]

    tags = ["fleet", "fworker", f"wq{ticket_id}"]
    if last.get("lane"):
        tags.append(_cli_item(last["lane"]))
    # wq#2901: the source_ref must be `wq#N` — memoryd derives the lane from the
    # ticket and its SOURCE_REF_RE takes nothing else (kernel.py:22), so a path
    # here was an unattributable write on every fleet episode. The path is raw
    # evidence, so it moves into `why` rather than being dropped. (It also used to
    # be computed without runs_root, i.e. wrong under FOREMAN_RUNS_ROOT.)
    evidence = episode_path(ticket_id, runs_root)
    if why:
        why = f"{why} [episode: {evidence}]"
    else:
        why = f"[episode: {evidence}]"
    return {
        "summary": summary[:200],
        "changed": changed,
        "failed": failed,
        "why": why,
        "kind": ("code-ship" if classification in _SUCCESS_CLASSIFICATIONS
                 else "session"),
        "tags": tags,
        "ref": f"wq#{ticket_id}",
        "evidence": evidence,
    }


def commit_to_archive(ticket_id, runs_root=None, episode_bin=None, timeout=30):
    """Commit the episode digest to archival_notes via lumen_episode.py commit
    (which tries memoryd /commit first — trust derives from the wq claim named
    in $WQ_AGENT — and falls back to a direct audited write). Returns the
    digest on commit, None when there was nothing to commit. Callers hold the
    fail-open contract: fworker wraps this in try/except.

    wq#2901: --ref is `wq#N`, which is what memoryd needs to derive the lane from
    the claim this worker holds on that ticket. Passing the episode PATH here made
    every fleet episode unattributable (design
    minerva/v4/research/fworker_lane_attribution.md D2)."""
    digest = digest_events(ticket_id, read_events(ticket_id, runs_root),
                           runs_root=runs_root)
    if digest is None:
        return None
    cmd = [
        sys.executable, episode_bin or episode_bin_path(), "commit",
        "--summary", digest["summary"],
        "--changed", ", ".join(digest["changed"]),
        "--failed", ", ".join(digest["failed"]),
        "--why", digest["why"],
        "--kind", digest["kind"],
        "--tags", ", ".join(digest["tags"]),
        "--ref", digest["ref"],
    ]
    subprocess.run(cmd, check=True, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return digest


def sweep_episodes(days=30, runs_root=None):
    """episode.jsonl is raw evidence, kept 30 days (wq#1635) — the durable
    record is the archival note. Removes episode files whose mtime is older
    than the cutoff; returns the paths removed."""
    root = runs_root or DEFAULT_RUNS_ROOT
    cutoff = time.time() - days * 86400
    removed = []
    for path in glob.glob(os.path.join(root, "wq*", EPISODE_FILENAME)):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed.append(path)
        except OSError:
            continue
    return removed
