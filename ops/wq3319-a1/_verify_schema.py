#!/usr/bin/env python3
"""wq#3319 --verify helper.

Checks that commander-pool.example.json still uses only the keys
ops/wq3294-commander-ha/pool_failover.py actually reads, so a later schema
change to the live config loader doesn't silently invalidate the on-arrival
runbook's example (ops/wq3319-a1/README.md step 2).
"""
import json
import sys

KNOWN_KEYS = {
    "_comment",
    "interval_s",
    "probe_timeout_s",
    "fail_threshold",
    "rise_threshold",
    "backends",
    "on_demote",
    "on_promote",
}


def main() -> int:
    path = sys.argv[1]
    with open(path) as f:
        cfg = json.load(f)

    extra = set(cfg) - KNOWN_KEYS
    if extra:
        print(f"MISS unknown keys in {path}: {sorted(extra)}")
        return 1

    backends = cfg.get("backends")
    if not isinstance(backends, list) or not backends:
        print(f"MISS {path} has no backends entries")
        return 1
    for b in backends:
        if "name" not in b or "url" not in b:
            print(f"MISS backend entry missing name/url in {path}: {b}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
