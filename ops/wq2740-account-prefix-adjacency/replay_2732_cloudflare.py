"""wq#2740: what does a replay of wq#2732 say about rotation row a745825001bc, post-fix?

Prints every cloudflare record's fields (hash, prefix, accounted_in, today_closed) under
both rule tables, plus the run-wide counts wq#2733 ("2 values") and wq#2736 ("3 values")
cited and this pass could not reproduce -- corpus('2732') is live, so the counts move.
Hashes and paths only; no value is ever printed.

Slow (minutes): every unaccounted value costs a pass over the whole tracked tree. Run it
detached per ISSUE-LOG row 831, not under a `timeout` wrapper.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from harness import mask_replay  # noqa: E402

items = mask_replay.corpus("2732")
tracked = mask_replay.tracked_text()
for common in mask_replay.foreign_repos(items):
    tracked.update(mask_replay.committed_text(common))
for table in ("current", "legacy"):
    patterns = (mask_replay.CURRENT_PATTERNS if table == "current"
                else mask_replay.LEGACY_PATTERNS)
    records = mask_replay.replay(2732, patterns=patterns, tracked=tracked, items=items)
    loose = mask_replay.unaccounted(records)
    print(f"{table} table: {len(records)} values, {len(loose)} unaccounted")
    for r in records:
        if r["label"] == "cloudflare":
            print("  cloudflare:", json.dumps(
                {k: r[k] for k in ("value_sha256_12", "prefix", "value_len",
                                   "accounted_in", "still_matches_today",
                                   "today_closed", "first_seen_in")}))
