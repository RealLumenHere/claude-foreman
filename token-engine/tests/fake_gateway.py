#!/usr/bin/env python3
"""Loopback stand-in for the LiteLLM admin API (wq#2278 T10 gate replay).

Serves POST /key/generate and /key/delete on an ephemeral 127.0.0.1 port,
writes {"port": N} to argv[1] once bound, and appends one JSON line per
request to argv[1] + ".log" so the replay can assert the delete actually
arrived. The minted value is a fixed replay dummy; the literal is split so
reading this file never matches a mask pattern (wq#2290).
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

REPLAY_KEY = "sk-" + "replay" * 6


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode(errors="replace")
        with open(sys.argv[1] + ".log", "a") as f:
            f.write(json.dumps({
                "path": self.path,
                "auth_present": bool(self.headers.get("Authorization")),
                "key_in_body": REPLAY_KEY in body}) + "\n")
        if self.path == "/key/generate":
            out = {"key": REPLAY_KEY, "expires": "2099-01-01T00:00:00Z"}
        elif self.path == "/key/delete":
            out = {"deleted": True}
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def main():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    with open(sys.argv[1], "w") as f:
        json.dump({"port": srv.server_address[1]}, f)
    srv.serve_forever()


if __name__ == "__main__":
    main()
