#!/usr/bin/env python3
"""wq#895 — does `claude -p` on Max OAuth survive ANTHROPIC_BASE_URL -> local proxy?

A stdlib-only pass-through proxy binds 127.0.0.1, relays every request to
https://api.anthropic.com verbatim (headers, body, streamed response), and logs a
masked summary of what the CLI sent. One short Haiku `claude -p` call is then run
against it. Credential values are never written: only the auth scheme, token prefix
(first 12 chars) and length.

  python3 research/oauth_passthrough_probe.py [--workdir DIR] [--port N]

Spends one ~10-token Haiku call of real Max-plan quota. Exit 0 = session completed
with no auth failure AND the proxy saw traffic; 1 otherwise. Writes probe_report.json.
"""
import argparse
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UPSTREAM = "api.anthropic.com"
PROBE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_BIN = shutil.which("claude") or "/home/lumen/.local/bin/claude"
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "host"}
PARENT_SESSION_VARS = (
    "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_PID",
)


def mask_auth(headers: dict) -> dict:
    auth = headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    return {
        "authorization_scheme": scheme or None,
        "authorization_prefix": token[:12] or None,
        "authorization_len": len(token),
        "has_x_api_key": "x-api-key" in headers,
        "anthropic_beta": headers.get("anthropic-beta"),
        "user_agent": headers.get("user-agent"),
    }


class PassThrough(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), _Handler)
        self.log: list[dict] = []
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _relay(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        headers = {k.lower(): v for k, v in self.headers.items()}
        fwd = {k: v for k, v in headers.items() if k not in HOP_BY_HOP}
        conn = http.client.HTTPSConnection(UPSTREAM, timeout=300)
        conn.request(self.command, self.path, body=body or None, headers=fwd)
        resp = conn.getresponse()
        entry = {"method": self.command, "path": self.path, "status": resp.status,
                 "req_body_bytes": len(body), **mask_auth(headers)}
        with self.server.lock:
            self.server.log.append(entry)
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in HOP_BY_HOP and k.lower() != "content-length":
                self.send_header(k, v)
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        while chunk := resp.read(4096):
            self.wfile.write(chunk)
            self.wfile.flush()
        conn.close()

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _relay

    def log_message(self, *args):
        pass


def run_claude(base_url: str | None, out: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in PARENT_SESSION_VARS and k != "ANTHROPIC_BASE_URL"}
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    cmd = [CLAUDE_BIN, "-p", "Reply with exactly the word: pong", "--model", PROBE_MODEL,
           "--output-format", "json", "--tools", ""]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    out.write_text(proc.stdout)
    try:
        r = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"rc": proc.returncode, "parse_error": True, "stderr": proc.stderr[:300]}
    return {"rc": proc.returncode, "subtype": r.get("subtype"), "is_error": r.get("is_error"),
            "result": r.get("result"), "duration_ms": r.get("duration_ms")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", type=Path, default=None)
    ap.add_argument("--port", type=int, default=8895)
    args = ap.parse_args()
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="wq895-"))
    workdir.mkdir(parents=True, exist_ok=True)

    server = PassThrough(args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        live = run_claude(f"http://127.0.0.1:{args.port}", workdir / "proxied.json")
    finally:
        server.shutdown()

    msgs = [e for e in server.log if e["path"].startswith("/v1/messages")]
    ok = (live.get("rc") == 0 and live.get("is_error") is False
          and live.get("subtype") == "success" and bool(msgs)
          and all(e["status"] < 400 for e in server.log))
    report = {"verdict": "PASS" if ok else "FAIL", "claude": live,
              "proxy_saw_messages": len(msgs), "proxy_log": server.log}
    (workdir / "probe_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
