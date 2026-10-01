#!/usr/bin/env python3
"""wq#3264 probe: minimal stdio JSON-RPC server for a throwaway supergateway.

Every request spawns two sleepers and logs their PIDs to $PIDLOG:
  plain   `sleep 241`         stays in the request's process group/session
  setsid  `setsid sleep 242`  new session, which the #108 reaper is meant to spare
Then it answers: immediately for most methods, after $SLOW_S seconds for tools/call
(so a client that gives up first produces the Mode B abort).
"""
import json
import os
import subprocess
import sys
import time

PIDLOG = os.environ["PIDLOG"]
SLOW_S = float(os.environ.get("SLOW_S", "10"))

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg or "method" not in msg:
        continue
    plain = subprocess.Popen(["sleep", "241"])
    detached = subprocess.Popen(["sleep", "242"], start_new_session=True)
    with open(PIDLOG, "a") as f:
        f.write(f"{msg['method']} plain={plain.pid} setsid={detached.pid}\n")
    if msg["method"] == "tools/call":
        time.sleep(SLOW_S)
    if msg["method"] == "initialize":
        result = {"protocolVersion": msg["params"].get("protocolVersion", "2024-11-05"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "wq3264-stub", "version": "0"}}
    else:
        result = {"content": [{"type": "text", "text": "ok"}]}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
