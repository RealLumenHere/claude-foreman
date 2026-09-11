#!/usr/bin/env python3
"""wq#1799 E.1/E.2: re-score FALSE_COMPLETE token-pool attempts against the CLI's
"API Error:" retry banner, per FWORKER_FIXES_FROM_HR_TEARDOWN.md's E.2 finding
(classifier.py lacked the scrub classifier.py:_API_ERROR_RETRY_RE now applies).

Re-scoring needs each attempt's LAST assistant text block. This fleet's fworker
does not persist raw stream-json captures under _runs/wq<ID>/ (only attempt-N.json,
usage-N.json, and — for attempts after wq#1183 — episode.jsonl's lossy last-6-
blocks tail via assistant_text_tail()). Full CLI transcripts live under
~/.claude/projects/, outside this worktree's allowed directories, so they are not
readable here. This script re-scores every attempt for which real text IS
available and reports the coverage gap honestly for the rest — it does not
fabricate a verdict for attempts with no retrievable text.

Read-only: never writes to _runs.
"""
import glob
import json
import os
import re

RUNS = "/home/lumen/foreman/_runs"
API_ERROR_RETRY_RE = re.compile(r"^\s*\[?API Error: ")


def load_false_complete_oauth_attempts():
    rows = []
    for ap in glob.glob(os.path.join(RUNS, "wq*", "attempt-*.json")):
        try:
            a = json.load(open(ap))
        except (OSError, ValueError):
            continue
        if a.get("classification") == "FALSE_COMPLETE" and a.get("oauth_token_used"):
            a["_path"] = ap
            rows.append(a)
    return rows


def episode_events(ticket):
    path = os.path.join(RUNS, f"wq{ticket}", "episode.jsonl")
    if not os.path.exists(path):
        return None
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def main():
    attempts = load_false_complete_oauth_attempts()
    no_episode_file = []
    no_matching_attempt_end = []
    empty_tail = []
    has_tail = []

    for a in attempts:
        events = episode_events(a["ticket"])
        if events is None:
            no_episode_file.append(a)
            continue
        matches = [ev for ev in events
                   if ev.get("type") == "attempt_end" and ev.get("attempt") == a.get("attempt")]
        if not matches:
            no_matching_attempt_end.append(a)
            continue
        tail = matches[0].get("assistant_tail") or []
        if not tail:
            empty_tail.append(a)
            continue
        has_tail.append((a, tail))

    rescored_api_error = []
    confirmed_real_failure = []
    for a, tail in has_tail:
        last = tail[-1] or ""
        if API_ERROR_RETRY_RE.search(last):
            rescored_api_error.append((a["ticket"], a["attempt"], last[:160]))
        else:
            confirmed_real_failure.append(a)

    no_data = len(no_episode_file) + len(no_matching_attempt_end) + len(empty_tail)

    print(f"FALSE_COMPLETE token-pool (oauth_token_used=True) attempts: {len(attempts)}")
    print(f"  no episode.jsonl at all (pre-dates wq#1183 or write failed): {len(no_episode_file)}")
    print(f"  episode.jsonl exists but no attempt_end for this attempt#:  {len(no_matching_attempt_end)}")
    print(f"  attempt_end matched but assistant_tail was empty:           {len(empty_tail)}")
    print(f"  -> no retrievable last-assistant-text, cannot re-score:     {no_data}")
    print(f"  has retrievable last-assistant-text, re-scorable:          {len(has_tail)}")
    print()
    print(f"re-scored as API-error retry banner (^\\s*\\[?API Error: on last block): "
          f"{len(rescored_api_error)}")
    for t in rescored_api_error:
        print(f"  wq{t[0]} attempt {t[1]}: {t[2]!r}")
    print(f"confirmed real failure (has text, does not match): {len(confirmed_real_failure)}")


if __name__ == "__main__":
    main()
