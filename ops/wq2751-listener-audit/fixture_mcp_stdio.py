#!/usr/bin/env python3
"""Minimal stdio MCP server for test_wq2751.py: answers initialize and tools/list, nothing else."""
import json
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
                  "serverInfo": {"name": "wq2751-fixture", "version": "0"}}
    elif msg.get("method") == "tools/list":
        result = {"tools": [{"name": "wq2751_echo", "description": "fixture",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
