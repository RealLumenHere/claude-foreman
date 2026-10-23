#!/usr/bin/env python3
"""wq#4555: drop the fleet-memory MCP server entry from a Claude session config.

Post wq#4554 (phase 2) its execute_sql targets the frozen PG memory tables, so
it serves nothing a session should touch; recipe access stays code-only via
recipe_store.

Usage: remove_mcp_entry.py [path]   (default: ~/.claude.json)
Backs up to <path>.bak.wq4555 before writing. Idempotent.
"""
import json
import shutil
import sys
from pathlib import Path

ENTRY = "fleet-memory"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".claude.json"
    data = json.loads(path.read_text())
    removed = []

    def prune(servers, where):
        if isinstance(servers, dict) and ENTRY in servers:
            del servers[ENTRY]
            removed.append(where)

    prune(data.get("mcpServers"), "top-level mcpServers")
    for proj, cfg in (data.get("projects") or {}).items():
        if isinstance(cfg, dict):
            prune(cfg.get("mcpServers"), f"projects[{proj}]")

    if not removed:
        print(f"no {ENTRY} entry in {path} — nothing to do")
        return 0

    backup = Path(f"{path}.bak.wq4555")
    shutil.copy2(path, backup)
    path.write_text(json.dumps(data, indent=2))
    print(f"removed {ENTRY} from: {', '.join(removed)} (backup: {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
