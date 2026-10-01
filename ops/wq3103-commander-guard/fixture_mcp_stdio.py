#!/usr/bin/env python3
"""wq#3103 stdio MCP fixture: like wq2751's, but initialize reports the NODE_OPTIONS it inherited,
so the test can prove the guard preload is NOT inherited by Commander's child processes."""
import json
import os
import sys

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue
    if msg.get("method") == "initialize":
        result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "wq3103-fixture", "version": "0",
                                 "childNodeOptions": os.environ.get("NODE_OPTIONS"),
                                 "childPreloadRole": os.environ.get("COMMANDER_PRELOAD_ROLE"),
                                 "childGuardPort": os.environ.get("ACCESS_GUARD_PORT")}}
    elif msg.get("method") == "tools/list":
        result = {"tools": [{"name": "wq3103_echo", "description": "fixture",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
