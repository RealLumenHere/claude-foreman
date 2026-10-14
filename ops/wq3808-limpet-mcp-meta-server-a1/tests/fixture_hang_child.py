"""Fixture child MCP server that stops answering, to exercise the proxy's read
timeouts (wq#3815 bug 3, fixed under wq#3821).

argv[1] picks where it goes silent:
  initialize (default) — reads requests, never writes anything at all.
  call               — completes the handshake and tools/list, then never
                       answers tools/call. This is the case the startup
                       timeout alone does NOT cover.

It keeps reading stdin (so writes to it don't block on a full pipe) and never
exits on its own; the client is expected to kill it.
"""
import json
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "initialize"


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line or MODE == "initialize":
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
                    "serverInfo": {"name": "fixture-hang-child", "version": "0.1"},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "never_answers",
                            "description": "accepts a call and goes silent",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            }
        else:
            # tools/call and everything else: silence.
            continue

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
