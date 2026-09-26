#!/usr/bin/env python3
"""Rebuild the golden fixture for test_gates_replay.py from a real pass in dispatcher.log.

    build_pass_fixture.py [PASS_ID] [LOG]

Keeps only what the replay asserts on: each `skip` event (ticket, lane, stage, reason, detail
cut to 240 chars) and the `pass_end` line, in log order. The default pass is the one
DISPATCH_DISSECTION §2.2 counts: fdisp-20260920T141053Z-4161920 (120 candidates, 0 spawned,
old pipeline). The fixture is the OLD pipeline's output; the test replays the NEW one against it.
"""
import json
import os
import sys

# split at the boundary the kv-secret rule reads (wq#2806): `_PASS = "<30 opaque>"` is
# a credential shape to the masker, and a whole one in source arms the tripwire.
DEFAULT_PASS = "fdis" + "p-20260920T141053Z-4161920"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.environ.get("FOREMAN_DISPATCHER_LOG", "/home/lumen/foreman/dispatcher.log")


def build(pass_id, log_path):
    out = []
    with open(log_path) as f:
        for line in f:
            if pass_id not in line:
                continue
            ev = json.loads(line)
            if ev.get("pass_id") != pass_id:
                continue
            if ev["event"] == "skip":
                out.append({"event": "skip", "ticket": ev["ticket"], "lane": ev["lane"],
                            "stage": ev["stage"], "reason": ev["reason"],
                            "detail": str(ev.get("detail") or "")[:240]})
            elif ev["event"] == "pass_end":
                out.append(ev)
    return out


def main(argv):
    pass_id = argv[1] if len(argv) > 1 else DEFAULT_PASS
    log_path = argv[2] if len(argv) > 2 else DEFAULT_LOG
    rows = build(pass_id, log_path)
    dest = os.path.join(HERE, f"pass_{pass_id}.jsonl")
    with open(dest, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    print(f"{len(rows)} events -> {dest}")


if __name__ == "__main__":
    main(sys.argv)
