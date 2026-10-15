#!/usr/bin/env python3
"""wq#4006 — drop the given keys from the top-level mcpServers block of a
Claude Code user config (~/.claude.json), leaving everything else untouched.
Idempotent: a key already absent is a no-op.

Usage: remove_user_scope.py <claude_json_path> <key> [<key> ...]
"""
import json
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    path, keys = sys.argv[1], sys.argv[2:]

    with open(path) as fh:
        config = json.load(fh)

    servers = config.get("mcpServers", {})
    removed = [k for k in keys if k in servers]
    for k in removed:
        del servers[k]

    if not removed:
        print("unchanged: no matching keys in mcpServers")
        return 0

    config["mcpServers"] = servers
    with open(path, "w") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")
    print(f"removed from mcpServers: {', '.join(removed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
