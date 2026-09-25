#!/usr/bin/env python3
"""Loopback git-over-HTTP forge for the T1 gate replay (wq#2271).

Wraps `git http-backend` (CGI) behind a Basic-auth check so the replay can
prove the whole chain — helper → broker → handoff → git push — against a
server that actually enforces the credential, with no network and no real
token. The expected token arrives via FILE (--token-file), never argv.

    fake_git_forge.py --root <bare-repos-root> --user lumen --token-file f
prints "PORT=<n>" once bound, then serves until killed.
"""

import argparse
import base64
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def make_handler(project_root, expected_auth, log):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _serve(self):
            if self.headers.get("Authorization", "") != expected_auth:
                body = b"auth required\n"
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="forge"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            path, _, query = self.path.partition("?")
            length = int(self.headers.get("Content-Length") or 0)
            stdin = self.rfile.read(length) if length else b""
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "GIT_PROJECT_ROOT": project_root,
                "GIT_HTTP_EXPORT_ALL": "1",
                "REQUEST_METHOD": self.command,
                "PATH_INFO": path,
                "QUERY_STRING": query,
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                "CONTENT_LENGTH": str(length),
                "REMOTE_ADDR": self.client_address[0],
                "REMOTE_USER": "gate",
            }
            p = subprocess.run(["git", "http-backend"], input=stdin, env=env,
                               capture_output=True, timeout=60)
            raw_headers, _, body = p.stdout.partition(b"\r\n\r\n")
            status = 200
            headers = []
            for line in raw_headers.decode("latin-1").splitlines():
                key, _, value = line.partition(":")
                if key.strip().lower() == "status":
                    status = int(value.strip().split()[0])
                elif key and value:
                    headers.append((key.strip(), value.strip()))
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _serve

        def log_message(self, fmt, *args):
            log.write("forge: %s\n" % (fmt % args))
            log.flush()

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--user", default="lumen")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args()
    with open(args.token_file) as f:
        token = f.read().strip()
    expected = "Basic " + base64.b64encode(
        ("%s:%s" % (args.user, token)).encode()).decode()
    srv = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(os.path.abspath(args.root), expected, sys.stderr))
    print("PORT=%d" % srv.server_address[1], flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
