#!/usr/bin/env python3
"""mcp_probe — full MCP e2e probe: initialize + tools/list against a streamable-HTTP URL.

wq#3294 item D helper. Exit 0 iff BOTH calls return a JSON-RPC result. Used by
dc_deploy_canary.sh to prove a candidate DC build actually serves MCP before the
live service is touched; also usable as a one-shot acceptance probe for any backend:

    python3 mcp_probe.py http://localhost:31000/mcp
"""

import json
import sys
import urllib.error
import urllib.request

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

CALLS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "wq3294-canary", "version": "1.0"},
        },
    },
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
]


def call_ok(url: str, body: dict, timeout_s: float) -> bool:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=HEADERS
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if not 200 <= resp.status < 300:
                print(f"{body['method']}: http {resp.status}", file=sys.stderr)
                return False
            text = resp.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"{body['method']}: {e}", file=sys.stderr)
        return False
    if '"result"' not in text:
        print(f"{body['method']}: no result in response", file=sys.stderr)
        return False
    return True


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    url = argv[1]
    timeout_s = float(argv[2]) if len(argv) > 2 else 15.0
    for body in CALLS:
        if not call_ok(url, body, timeout_s):
            return 1
    print(f"OK {url}: initialize + tools/list both returned results")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
