#!/usr/bin/env python3
"""Minimal token-engine broker client (wq#2270).

Sends one verb over /run/token-engine/broker.sock and prints the response
frame. Carries only argv and grant *references* — never a secret. Used by the
T2 gate replay and as the seam fleet-exec grows its broker verbs onto (T1/T4).

  te_client.py --verb ping --ticket wq2270 --worker w1 \
      --grant-file /path/grant.json --grant-sha <sha256> [--argv a b ...]

Grant file/sha default from FOREMAN_GRANT_FILE / FOREMAN_GRANT_SHA, the same
env contract fleet-exec already uses. Exit 0 iff the broker said ok.
"""

import argparse
import json
import os
import socket
import sys

SOCK_PATH = os.environ.get("TOKEN_ENGINE_SOCK", "/run/token-engine/broker.sock")


def call(req, timeout=15):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK_PATH)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.split(b"\n", 1)[0].decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verb", required=True)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--worker", required=True)
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--grant-file", default=os.environ.get("FOREMAN_GRANT_FILE"))
    ap.add_argument("--grant-sha", default=os.environ.get("FOREMAN_GRANT_SHA"))
    ap.add_argument("--argv", nargs="*", default=[])
    args = ap.parse_args()
    resp = call({"verb": args.verb, "argv": args.argv, "ticket": args.ticket,
                 "attempt": args.attempt, "worker": args.worker,
                 "grant_file": args.grant_file, "grant_sha": args.grant_sha})
    print(json.dumps(resp, sort_keys=True))
    sys.exit(0 if resp.get("ok") else 1)


if __name__ == "__main__":
    main()
