#!/usr/bin/env python3
"""wq#4913 — patch the rea-maksoor entry in a project .mcp.json to route
through rea-guard on the remote side instead of calling the bare binary.

Usage: patch_mcp_json.py <mcp_json_path> <remote_guard_path>
"""
import json
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    mcp_path, guard_path = sys.argv[1], sys.argv[2]

    try:
        with open(mcp_path) as fh:
            config = json.load(fh)
    except FileNotFoundError:
        print(f"ERR: {mcp_path} not found", file=sys.stderr)
        return 1

    servers = config.get("mcpServers", {})
    entry = servers.get("rea-maksoor")
    if entry is None:
        print(f"ERR: no rea-maksoor entry in {mcp_path}", file=sys.stderr)
        return 1

    args = entry.get("args", [])
    if len(args) >= 2:
        old_binary = args[1]
        args[1] = guard_path
        print(f"  rea-maksoor args[1]: {old_binary} -> {guard_path}")
    else:
        print(f"ERR: rea-maksoor args has unexpected shape: {args}", file=sys.stderr)
        return 1

    with open(mcp_path, "w") as fh:
        json.dump(config, fh, indent=2)
        fh.write("\n")
    print(f"  wrote {mcp_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
