"""Minimal fixture MCP server (stdio, newline-delimited JSON-RPC) used by
tests to stand in for a real child like REA/Ghidra MCP/radare2 MCP without
needing those installed. Exposes one tool, "ping".
"""
import json
import sys


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        method = message.get("method")
        req_id = message.get("id")

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture-child", "version": "0.1"},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "ping",
                            "description": "replies pong",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            }
        elif method == "tools/call" and message.get("params", {}).get("name") == "ping":
            response = {"jsonrpc": "2.0", "id": req_id, "result": {"reply": "pong"}}
        else:
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"unknown method '{method}'"},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
