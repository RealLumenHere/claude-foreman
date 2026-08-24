#!/usr/bin/env python3
"""wq#889 — headroom proxy OAuth-bearer passthrough gate.

Three checks, in order of how much they cost to run:

  synthetic  Points headroom's Anthropic upstream at a local recorder and pushes one
             /v1/messages through it carrying a fake `sk-ant-oat01-...` bearer. Proves
             passthrough byte-for-byte without putting the real Max-plan credential on
             a wire we control. Also shows compression actually fired.

  envleak    Starts the proxy with ANTHROPIC_TARGET_API_URL set and no flag, to answer
             whether fworker's ANTHROPIC_BASE_URL scrub still holds once a proxy sits in
             the path. It does not fully: the redirect just moves into the proxy's env.

  live       Opt-in (--live). One real `claude -p` through the proxy on the real OAuth
             credential, plus the same prompt direct, for a wall-clock comparison.

Usage:
    python3 research/headroom_proxy_gate.py --venv /tmp/hr-venv
    python3 research/headroom_proxy_gate.py --venv /tmp/hr-venv --live

Setup:
    python3 -m venv /tmp/hr-venv
    /tmp/hr-venv/bin/pip install 'headroom-ai[proxy]'
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SYNTHETIC_BEARER = "Bearer sk-ant-oat01-WQ889SYNTHETIC-not-a-real-credential-0000000000"
CLAUDE_BIN = "/home/lumen/.npm-global/bin/claude"
PROBE_MODEL = "claude-haiku-4-5-20251001"

# Session vars a nested `claude -p` must not inherit from its parent worker.
PARENT_SESSION_VARS = (
    "CLAUDECODE",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_PID",
    "ANTHROPIC_BASE_URL",
)

UPSTREAM_REPLY = {
    "id": "msg_wq889_synthetic",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5",
    "content": [{"type": "text", "text": "pong"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 1234, "output_tokens": 4},
}


class RecordingUpstream(ThreadingHTTPServer):
    """Stands in for api.anthropic.com and keeps every request verbatim."""

    daemon_threads = True

    def __init__(self, port: int):
        super().__init__(("127.0.0.1", port), _RecordingHandler)
        self.captured: list[dict] = []


class _RecordingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        self.server.captured.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body_bytes": len(raw),
            }
        )
        payload = json.dumps(UPSTREAM_REPLY).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def start_recording_upstream(port: int) -> RecordingUpstream:
    server = RecordingUpstream(port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start_proxy(venv: Path, port: int, log_path: Path, *, extra_args=(), extra_env=None):
    env = dict(os.environ)
    env["HEADROOM_TELEMETRY"] = "off"
    env.update(extra_env or {})
    cmd = [
        str(venv / "bin" / "headroom"), "proxy",
        "--host", "127.0.0.1", "--port", str(port),
        "--no-http2", "--no-telemetry",
        *extra_args,
    ]
    log = log_path.open("w")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
    for _ in range(90):
        if proc.poll() is not None:
            raise RuntimeError(f"proxy exited early, see {log_path}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).read()
            return proc
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    proc.kill()
    raise RuntimeError(f"proxy never became healthy, see {log_path}")


def probe(port: int) -> tuple[int, int]:
    """One /v1/messages with a fat tool_result. Returns (status, bytes sent)."""
    rows = [
        {
            "id": i,
            "path": f"/home/lumen/worktrees/wq889-a1/file_{i}.py",
            "status": "unchanged",
            "size_bytes": 4096 + i,
            "sha": f"{i:040x}",
            "mtime": "2026-08-24T00:00:00Z",
        }
        for i in range(400)
    ]
    body = {
        "model": "claude-opus-5",
        "max_tokens": 64,
        "stream": False,
        "messages": [
            {"role": "user", "content": "list the files"},
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_wq889", "name": "list_files",
                     "input": {"path": "."}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_wq889",
                     "content": json.dumps({"rows": rows})}
                ],
            },
        ],
    }
    raw = json.dumps(body).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=raw,
        headers={
            "content-type": "application/json",
            "authorization": SYNTHETIC_BEARER,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "user-agent": "claude-cli/2.0.30 (external, cli)",
            "x-app": "cli",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, len(raw)
    except urllib.error.HTTPError as exc:
        exc.read()
        return exc.code, len(raw)


def fetch_stats(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=30) as resp:
        return json.loads(resp.read())


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def check_synthetic(venv: Path, workdir: Path, upstream_port: int, proxy_port: int) -> dict:
    upstream = start_recording_upstream(upstream_port)
    proxy = start_proxy(
        venv, proxy_port, workdir / "proxy_synthetic.log",
        extra_args=("--anthropic-api-url", f"http://127.0.0.1:{upstream_port}"),
    )
    try:
        status, sent_bytes = probe(proxy_port)
        stats = fetch_stats(proxy_port)
    finally:
        stop(proxy)
        upstream.shutdown()

    if not upstream.captured:
        return {"pass": False, "reason": "proxy forwarded nothing upstream"}
    seen = upstream.captured[0]
    auth = seen["headers"].get("authorization", "")
    compression = stats.get("summary", {}).get("compression", {})
    return {
        "pass": status == 200 and auth == SYNTHETIC_BEARER,
        "status": status,
        "auth_byte_identical": auth == SYNTHETIC_BEARER,
        "x_api_key_injected": "x-api-key" in seen["headers"],
        "oauth_beta_preserved": seen["headers"].get("anthropic-beta") == "oauth-2025-04-20",
        "user_agent_preserved": seen["headers"].get("user-agent", "").startswith("claude-cli/"),
        "forwarded_header_keys": sorted(seen["headers"]),
        "body_bytes_client_to_proxy": sent_bytes,
        "body_bytes_proxy_to_upstream": seen["body_bytes"],
        "requests_compressed": compression.get("requests_compressed"),
        "avg_compression_pct": compression.get("avg_compression_pct"),
        "tokens_removed": compression.get("total_tokens_removed"),
    }


def check_envleak(venv: Path, workdir: Path, upstream_port: int, proxy_port: int) -> dict:
    """No --anthropic-api-url flag: only ANTHROPIC_TARGET_API_URL in the proxy's env."""
    upstream = start_recording_upstream(upstream_port)
    proxy = start_proxy(
        venv, proxy_port, workdir / "proxy_envleak.log",
        extra_env={"ANTHROPIC_TARGET_API_URL": f"http://127.0.0.1:{upstream_port}"},
    )
    try:
        status, _ = probe(proxy_port)
    finally:
        stop(proxy)
        upstream.shutdown()

    banner = (workdir / "proxy_envleak.log").read_text()
    routing = next(
        (ln.strip() for ln in banner.splitlines() if "/v1/messages" in ln and "→" in ln), ""
    )
    redirected = bool(upstream.captured)
    return {
        # A "pass" here means the redirect is real AND the startup banner names the
        # host it redirected to — the risk is silent redirection, not redirection.
        "pass": redirected and f"127.0.0.1:{upstream_port}" in routing,
        "status": status,
        "env_var_redirected_upstream": redirected,
        "banner_routing_line": routing,
    }


def run_claude(prompt: str, out: Path, *, base_url: str | None, extra_args=()) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in PARENT_SESSION_VARS}
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    cmd = [CLAUDE_BIN, "-p", prompt, "--model", PROBE_MODEL, "--output-format", "json",
           *extra_args]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    out.write_text(proc.stdout)
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"rc": proc.returncode, "parse_error": True, "stderr": proc.stderr[:400]}
    return {
        "rc": proc.returncode,
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "result": result.get("result"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
    }


def check_live(venv: Path, workdir: Path, proxy_port: int) -> dict:
    marker = workdir / "marker.txt"
    marker.write_text("ratchet\n")
    proxy = start_proxy(venv, proxy_port, workdir / "proxy_live.log")
    base_url = f"http://localhost:{proxy_port}"
    try:
        plain = run_claude(
            "Reply with exactly one word: pong",
            workdir / "claude_proxy.json", base_url=base_url,
        )
        tools = run_claude(
            f"Use the Read tool on {marker} and reply with only the word it contains.",
            workdir / "claude_proxy_tools.json", base_url=base_url,
            extra_args=("--allowedTools", "Read"),
        )
        stats = fetch_stats(proxy_port)
    finally:
        stop(proxy)

    direct = run_claude(
        "Reply with exactly one word: pong", workdir / "claude_direct.json", base_url=None
    )
    compression = stats.get("summary", {}).get("compression", {})
    deferrals = [
        t
        for log in stats.get("request_logs", [])
        for t in log.get("transforms_applied", [])
    ]
    return {
        "pass": (
            plain.get("subtype") == "success"
            and not plain.get("is_error")
            and tools.get("subtype") == "success"
            and str(tools.get("result", "")).strip() == "ratchet"
            and bool(compression.get("requests_compressed"))
        ),
        "session_through_proxy": plain,
        "tool_session_through_proxy": tools,
        "session_direct_baseline": direct,
        "requests_compressed": compression.get("requests_compressed"),
        "avg_compression_pct": compression.get("avg_compression_pct"),
        "tool_schema_tokens_saved": compression.get("tool_schema_tokens_saved"),
        "transforms_applied": sorted(set(deferrals)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--venv", required=True, type=Path,
                    help="venv with 'headroom-ai[proxy]' installed")
    ap.add_argument("--live", action="store_true",
                    help="also run real `claude -p` sessions (spends real quota)")
    ap.add_argument("--workdir", type=Path, default=None)
    ap.add_argument("--base-port", type=int, default=8890)
    args = ap.parse_args()

    if not (args.venv / "bin" / "headroom").exists():
        print(f"no headroom CLI in {args.venv}; see the setup notes in this file's docstring")
        return 2

    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="wq889-"))
    workdir.mkdir(parents=True, exist_ok=True)
    p = args.base_port

    report = {
        "synthetic": check_synthetic(args.venv, workdir, p, p + 1),
        "envleak": check_envleak(args.venv, workdir, p + 2, p + 3),
    }
    if args.live:
        if not Path(CLAUDE_BIN).exists():
            report["live"] = {"pass": False, "reason": f"no claude CLI at {CLAUDE_BIN}"}
        else:
            report["live"] = check_live(args.venv, workdir, p + 4)

    (workdir / "gate_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nartifacts: {workdir}", file=sys.stderr)
    return 0 if all(section.get("pass") for section in report.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
