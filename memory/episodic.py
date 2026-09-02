#!/usr/bin/env python3
"""episodic.py — the EPISODIC half of fleet agent memory (wq#1183).

Pattern C of kb/fleet-architecture-ideas.md §1: memory splits into an append-only
EPISODIC log (this module) and a DISTILLED store of durable facts (distill.py +
recall.py), with an explicit extraction step between them. Agents never write the
distilled store directly — they only ever append episodes here; a separate
distiller pass (distill.py) reads episodes and emits facts.

One episode file per ticket run, alongside the other run artifacts fworker
already writes:

    RUNS_ROOT/wq<id>/episode.jsonl      one JSON object per line, append-only

Events carry their attempt number, so a multi-attempt run is one episode with
several attempt_end events. Nothing here ever truncates or rewrites — the log is
the raw record, replayable and re-distillable with a better prompt later.

Stdlib-only (repo convention). Writers must be fail-open: fworker wraps every
call in try/except, so raising here can never fail an attempt.
"""

import json
import os
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
