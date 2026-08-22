#!/usr/bin/env python3
"""claude-shim — OpenAI-compatible /v1/chat/completions over the Claude Max 20x
session (wq#726 / research/CLAUDE_SHIM_DESIGN.md).

Fronts `claude -p` (OAuth session, $0/call marginal cost) as a localhost HTTP
endpoint LiteLLM can register like any other model row. Each request is one
`claude -p` subprocess call using the exact no-tools/no-session/no-MCP flags
proven live by messenger's `_try_claude_sync`
(/home/lumen/messenger/agent_bridge.py:2489) — this module wraps that call in
HTTP, it does not invent a new CLI incantation.

Design doc (read first, covers every decision below): research/CLAUDE_SHIM_DESIGN.md

Contract, briefly:
  POST /v1/chat/completions  -> OpenAI chat-completion shape in/out, non-streaming only.
  GET  /v1/models            -> gateway-discovery listing (§8 of the design doc).
  GET  /health                -> 200 liveness probe, no CLI call.
Every failure is a non-2xx response with a body explaining why (design doc §6,
"fail LOUD" — this is the wq#722 anti-pattern the shim is built to avoid).

Usage:
  claude_shim.py run              # start the HTTP server (foreground)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

log = logging.getLogger("claude_shim")

CLAUDE_BIN = os.getenv("CLAUDE_BIN", "/home/lumen/.npm-global/bin/claude")
HOST = os.getenv("CLAUDE_SHIM_HOST", "0.0.0.0")
PORT = int(os.getenv("CLAUDE_SHIM_PORT", "8091"))
MAX_CONCURRENCY = int(os.getenv("CLAUDE_SHIM_MAX_CONCURRENCY", "2"))
QUEUE_WAIT_S = float(os.getenv("CLAUDE_SHIM_QUEUE_WAIT_S", "10"))
CLI_TIMEOUT_S = float(os.getenv("CLAUDE_SHIM_TIMEOUT_S", "120"))
API_KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".api_key")

# Docker-bridge-or-loopback only — design doc §7. Not a literal 127.0.0.1 bind
# because the LiteLLM container reaches this process via host.docker.internal,
# which resolves to a bridge-gateway IP, not loopback.
ALLOWED_NETS = [ip_network(n) for n in
                os.getenv("CLAUDE_SHIM_ALLOW_CIDRS", "127.0.0.0/8,172.16.0.0/12").split(",")]

MODEL_MAP = {
    "claude-max-sonnet": "sonnet",
    "claude-max-opus": "opus",
}

# Sampling params the CLI has no flag for — dropped, not rejected (design doc §4).
_IGNORED_PARAMS = ("max_tokens", "temperature", "top_p", "stop", "n",
                    "presence_penalty", "frequency_penalty", "seed", "logprobs", "user")
_warned_params: set[str] = set()

_sem = threading.Semaphore(MAX_CONCURRENCY)


def _load_or_create_api_key() -> str:
    key = os.getenv("CLAUDE_SHIM_API_KEY", "")
    if key:
        return key
    if os.path.exists(API_KEY_PATH):
        with open(API_KEY_PATH) as f:
            return f.read().strip()
    key = secrets.token_urlsafe(32)
    fd = os.open(API_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    log.warning("generated new API key, saved to %s (Bearer auth required)", API_KEY_PATH)
    return key


API_KEY = _load_or_create_api_key()


def _format_prompt(messages: list) -> tuple[str, str]:
    """Flatten OpenAI-style messages into (system_prompt, transcript_prompt).

    Mirrors agent_bridge.py's _format_history_as_prompt: first system message
    goes to --system-prompt, everything else becomes a User/Assistant transcript.
    """
    system_prompt = "You are Claude."
    parts = []
    seen_system = False
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if not isinstance(content, str):
            try:
                content = json.dumps(content, default=str)
            except Exception:
                content = str(content)
        if role == "system":
            if not seen_system:
                system_prompt = content
                seen_system = True
            continue
        if role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
    return system_prompt, "\n\n".join(parts)


class ShimError(Exception):
    def __init__(self, status: int, err_type: str, message: str):
        super().__init__(message)
        self.status = status
        self.err_type = err_type
        self.message = message


def _validate_request(body: dict) -> tuple[str, str, list]:
    model = body.get("model")
    if model not in MODEL_MAP:
        raise ShimError(400, "invalid_request",
                         f'unknown model "{model}", must be one of {sorted(MODEL_MAP)}')
    if body.get("stream"):
        raise ShimError(400, "invalid_request", "stream=true not supported")
    if body.get("tools") or body.get("functions") or body.get("tool_choice"):
        raise ShimError(400, "invalid_request", "tools/functions not supported (no-tools completion path)")
    if body.get("n", 1) not in (1, None):
        raise ShimError(400, "invalid_request", "n>1 not supported")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ShimError(400, "invalid_request", "messages must be a non-empty list")
    for p in _IGNORED_PARAMS:
        if p in body and p not in _warned_params:
            _warned_params.add(p)
            log.warning("param %r not supported by claude -p, ignoring (logged once)", p)
    return model, MODEL_MAP[model], messages


def _classify_cli_result(returncode: int, stdout: str, stderr: str) -> tuple:
    """Returns (http_status, err_type, text_or_None, detail)."""
    if not stdout.strip():
        return 502, "cli_crash", None, (stderr or "empty stdout")[:300]
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return 502, "cli_crash", None, f"unparseable stdout: {stdout[:300]}"
    if not isinstance(data, dict):
        return 502, "cli_crash", None, f"unexpected JSON shape: {stdout[:300]}"

    if data.get("is_error"):
        subtype = data.get("subtype", "")
        rate_limited = (
            subtype == "error_during_execution"
            and (data.get("rate_limits") is not None
                 or "rate_limit" in json.dumps(data.get("errors", [])).lower())
        )
        if rate_limited:
            return 503, "rate_limited", None, data.get("result") or stdout[:300]
        if subtype == "error_max_turns":
            return 500, "cli_max_turns", None, "unexpected: shim passes no --max-turns"
        if subtype == "error_max_budget_usd":
            return 500, "cli_max_budget", None, "unexpected: shim passes no --max-budget-usd"
        return 502, "cli_error", None, (data.get("result") or json.dumps(data.get("errors", [])))[:300]

    text = data.get("result") or data.get("text") or ""
    if not isinstance(text, str):
        text = str(text)
    return 200, None, text.strip(), data


def _call_claude(model_alias: str, system_prompt: str, prompt: str) -> tuple:
    if "ANTHROPIC_API_KEY" in os.environ:
        # Should never happen — startup self-check already refused to run in
        # this case. Defense in depth: never let a call through on the wrong
        # billing path.
        raise ShimError(500, "config_error", "ANTHROPIC_API_KEY set in shim env — refusing (OAuth-only)")

    cmd = [
        CLAUDE_BIN, "-p",
        "--output-format", "json",
        "--model", model_alias,
        "--system-prompt", system_prompt,
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--tools", "",
        "--no-session-persistence",
        prompt,
    ]
    acquired = _sem.acquire(timeout=QUEUE_WAIT_S)
    if not acquired:
        raise ShimError(429, "overloaded", f"no CLI slot available within {QUEUE_WAIT_S}s")
    try:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_S,
                stdin=subprocess.DEVNULL, env=dict(os.environ),
            )
        except subprocess.TimeoutExpired:
            raise ShimError(504, "cli_timeout", f"claude -p exceeded {CLI_TIMEOUT_S}s")
    finally:
        _sem.release()

    status, err_type, text, detail = _classify_cli_result(proc.returncode, proc.stdout or "", proc.stderr or "")
    if status != 200:
        log.warning("cli call failed model=%s status=%s type=%s detail=%s",
                     model_alias, status, err_type, str(detail)[:300])
        raise ShimError(status, err_type, str(detail)[:500])
    return text, detail


def _openai_response(model: str, text: str, cli_data: dict) -> dict:
    usage = cli_data.get("usage", {}) if isinstance(cli_data, dict) else {}
    return {
        "id": f"claudeshim-{secrets.token_hex(8)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "claude-shim/0.1"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.client_address[0], fmt % args)

    def _client_allowed(self) -> bool:
        try:
            addr = ip_address(self.client_address[0])
        except ValueError:
            return False
        return any(addr in net for net in ALLOWED_NETS)

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, err_type: str, message: str):
        self._send_json(status, {"error": {"type": err_type, "message": message}})

    def _authed(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {API_KEY}"

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if not self._client_allowed():
            self._error(403, "forbidden", "source not in allowlist")
            return
        if path == "/v1/models":
            if not self._authed():
                self._error(401, "unauthorized", "missing/invalid bearer token")
                return
            self._send_json(200, {"object": "list", "data": [
                {"id": m, "object": "model"} for m in MODEL_MAP
            ]})
            return
        self._error(404, "not_found", f"no such path: {path}")

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/v1/chat/completions":
            self._error(404, "not_found", f"no such path: {path}")
            return
        if not self._client_allowed():
            self._error(403, "forbidden", "source not in allowlist")
            return
        if not self._authed():
            self._error(401, "unauthorized", "missing/invalid bearer token")
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            self._error(400, "invalid_request", "malformed JSON body")
            return
        try:
            model, alias, messages = _validate_request(body)
            system_prompt, prompt = _format_prompt(messages)
            text, cli_data = _call_claude(alias, system_prompt, prompt)
            self._send_json(200, _openai_response(model, text, cli_data))
        except ShimError as e:
            self._error(e.status, e.err_type, e.message)
        except Exception as e:  # noqa: BLE001 — last-resort fail-loud, never swallow
            log.exception("unhandled error")
            self._error(502, "cli_crash", str(e)[:300])


def _startup_self_check():
    if "ANTHROPIC_API_KEY" in os.environ:
        log.error("ANTHROPIC_API_KEY is set in the shim's environment — refusing to start "
                   "(this shim is OAuth-only; the API-key path bills per-token, defeating "
                   "the point of wq#726). Unset it and restart.")
        sys.exit(1)
    if not os.path.exists(CLAUDE_BIN):
        log.error("CLAUDE_BIN not found at %s", CLAUDE_BIN)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="start the HTTP server (foreground)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd == "run":
        _startup_self_check()
        server = ThreadingHTTPServer((HOST, PORT), Handler)
        log.info("claude-shim listening on %s:%d (max_concurrency=%d, allow=%s)",
                  HOST, PORT, MAX_CONCURRENCY, [str(n) for n in ALLOWED_NETS])
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
