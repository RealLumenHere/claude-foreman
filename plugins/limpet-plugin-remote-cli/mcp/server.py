#!/usr/bin/env python3
"""limpet-remote-cli-mcp — MCP server wrapping remote-cli, the shared-PTY
terminal tool wq#4395 installs fleet-wide (Pure Python, Unix socket IPC, no
tmux/screen dependency — design/wq3806_limpet_architecture.md's bench-RE
"eliminate copy-paste and screenshot workflow" need).

wq#4396: remote-cli becomes Limpet's shared terminal backend. Four tools:
exec (run a command), snapshot (read current screen state), send (type raw
keystrokes), logs (scrollback). Frank and an agent attach to the SAME PTY
session remote-cli owns, so every one of these tools is a side effect on a
terminal a human may be watching live — never a private sandbox.

Energize classification (ticket + this plugin's own judgment call):
  snapshot = read_only   — reads screen state, no write to the PTY
  logs     = read_only   — reads scrollback, no write to the PTY
  send     = energizing  — ticket-specified; raw keystrokes into a terminal
             a human may be driving (passwords, control sequences, a stray
             Ctrl-C) are exactly what the energize-confirm gate exists for.
  exec     = energizing  — NOT named in the ticket's classification list, but
             the same reasoning applies one step further: exec runs a whole
             command in the shared session. It is strictly more consequential
             than send (an arbitrary command vs. raw keys), so gating send
             but not exec would leave the riskier of the two tools ungated.
             Declared accordingly in limpet-plugin.toml's [mcp.tools_energize].

This server owns the MCP protocol surface; remote-cli itself is not an MCP
server; so, unlike limpet-plugin-serial-mcp (which wraps a third-party MCP
package), this is first-party code in the same vein as limpet-plugin-flashrom's
mcp/server.py. Protocol to the remote-cli daemon: connect to its Unix domain
socket, write one JSON object + "\\n", read one JSON object + "\\n" back —
the wire shape wq4395's install documents for the shared session socket.
Stdlib-only, same rationale as classify_access.py: unit-testable without the
`mcp` SDK or a live remote-cli daemon.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from typing import Any, Dict, Optional

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "limpet-remote-cli-mcp"
SERVER_VERSION = "0.1.0"

DEFAULT_SOCKET_PATH = "/run/remote-cli/shared.sock"
SOCKET_PATH = os.environ.get("REMOTE_CLI_SOCKET_PATH", DEFAULT_SOCKET_PATH)
DEFAULT_TIMEOUT = 5.0


class RemoteCliError(RuntimeError):
    """Raised when the remote-cli Unix socket can't be reached or misbehaves."""


class RemoteCliClient:
    """One-shot request/response client for remote-cli's shared-session
    socket: connect, send a JSON line, read a JSON line back, disconnect.
    remote-cli owns the long-lived PTY; this client never holds the
    connection open between tool calls, so concurrent agent + human access
    to the same session is remote-cli's concern, not this client's."""

    def __init__(self, socket_path: str = SOCKET_PATH, timeout: float = DEFAULT_TIMEOUT):
        self._socket_path = socket_path
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        try:
            sock.connect(self._socket_path)
        except OSError as exc:
            sock.close()
            raise RemoteCliError(
                f"cannot reach remote-cli at {self._socket_path}: {exc}") from exc
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def request(self, op: str, **fields: Any) -> Dict[str, Any]:
        if self._sock is None:
            raise RemoteCliError("not connected; call connect() first")
        payload = json.dumps({"op": op, **fields}) + "\n"
        try:
            self._sock.sendall(payload.encode())
            chunks = []
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if chunks[-1].endswith(b"\n"):
                    break
        except OSError as exc:
            raise RemoteCliError(f"remote-cli request failed: {exc}") from exc
        raw = b"".join(chunks).decode(errors="replace").strip()
        if not raw:
            raise RemoteCliError("remote-cli closed the connection with no response")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RemoteCliError(f"remote-cli sent non-JSON response: {raw!r}") from exc


def _call(op: str, **fields: Any) -> Dict[str, Any]:
    """Open a fresh connection, issue one request, close it. Shared by all
    four tool functions so each is independently retryable."""
    client = RemoteCliClient()
    try:
        client.connect()
        return client.request(op, **fields)
    except RemoteCliError as exc:
        return {"error": str(exc)}
    finally:
        client.close()


def tool_exec(command: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Run a command in the shared terminal session. ENERGIZING — see module
    docstring: this mutates a PTY a human may be watching live."""
    return _call("exec", command=command, timeout=timeout)


def tool_snapshot() -> Dict[str, Any]:
    """Read the current screen state of the shared terminal. Read-only."""
    return _call("snapshot")


def tool_send(keys: str) -> Dict[str, Any]:
    """Type raw keystrokes into the shared terminal (no implied newline/command
    framing — use for control sequences, interactive prompts, arrow keys).
    ENERGIZING per the ticket: routed through the Sentinel energize-confirm
    gate before remote-cli ever sees the keys."""
    return _call("send", keys=keys)


def tool_logs(lines: int = 200) -> Dict[str, Any]:
    """Read the last `lines` of scrollback from the shared terminal. Read-only."""
    return _call("logs", lines=lines)


TOOLS = {
    "exec": {
        "description": "Run a command in the shared remote-cli terminal session. ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command line to run"},
                "timeout": {"type": "number", "description": "Seconds to wait for completion", "default": 30.0},
            },
            "required": ["command"],
        },
        "handler": tool_exec,
    },
    "snapshot": {
        "description": "Read the current screen state of the shared terminal. Read-only.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_snapshot,
    },
    "send": {
        "description": "Type raw keystrokes into the shared terminal. ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Raw keystrokes to send (e.g. control sequences, interactive prompt answers)"},
            },
            "required": ["keys"],
        },
        "handler": tool_send,
    },
    "logs": {
        "description": "Read scrollback from the shared terminal. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lines": {"type": "integer", "description": "Number of trailing scrollback lines to return", "default": 200},
            },
        },
        "handler": tool_logs,
    },
}


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC request. Mirrors limpet-plugin-flashrom/mcp/server.py's
    handle_request shape so every first-party Limpet MCP server in this repo
    speaks the same minimal stdio JSON-RPC subset."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {}) or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        }}
    elif method == "tools/list":
        tools = [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]}
                  for k, v in TOOLS.items()]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {}) or {}
        if tool_name not in TOOLS:
            return {"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32601, "message": f"unknown tool: {tool_name}"}}
        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
        except Exception as e:  # noqa: BLE001 — surface any handler failure as a tool error, not a crash
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True}}
    elif method == "notifications/initialized":
        return None  # notification, no response
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    """stdio JSON-RPC loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
