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
mcp/server.py. Stdlib-only, same rationale as classify_access.py: unit-testable
without the `mcp` SDK or a live remote-cli daemon.

Wire protocol to the remote-cli daemon (remote-cli:9-18 docstring,
remote-cli:271-292 dispatcher — the authoritative contract, read off the
deliverable wq#4395 installs): connect to its Unix domain socket, write one
JSON object + "\\n", read one JSON object + "\\n" back, connection closes.
The top-level key is "cmd" and the five commands are:

    {"cmd": "snapshot"}                         -> {"ok", "screen", "rows", "cols"}
    {"cmd": "scrollback", "lines": 200}         -> {"ok", "text"}
    {"cmd": "send_keys", "text": "...",
                         "enter": false}        -> {"ok"}
    {"cmd": "resize", "rows": 40, "cols": 120}  -> {"ok"}
    {"cmd": "status"}                           -> {"ok", "status"}

wq#4398 (judgment on wq#4397's CONTRADICT verdict): the first cut of this file
spoke an invented protocol — top-level "op", command names exec/snapshot/send/
logs — and every one of the four tools therefore got back
`{"ok": false, "error": "unknown cmd None"}` from the real daemon. The MCP tool
NAMES (exec/snapshot/send/logs, declared in limpet-plugin.toml's
[mcp.tools_energize]) are this plugin's own API surface and are unchanged; what
changed is the wire layer underneath, which now speaks the five commands above
and nothing else. tests/fixtures/remote_cli_protocol.json pins that command set
as a committed fixture, and tests/test_server.py drives the real daemon rather
than a fake built to this file's own assumptions (the hole that let the first
cut ship 8/8 green while 0/4 tools worked).

`exec` is synthesized, not native: remote-cli deliberately has no
run-and-wait-for-exit-code primitive — it owns a PTY a human may be attached
to, so the only way to run something IN that shared session is to type it.
tool_exec therefore sends the command plus a unique completion marker carrying
`$?`, then polls `scrollback` until the marker appears (the marker-anchored
extraction pattern deepseek-ai/dsh-tool-bash-persistent uses for the same
shared-PTY problem). This is a heuristic with real limits, documented on
tool_exec itself; it is implemented client-side here on purpose, so the
fleet-wide remote-cli binary already deployed on Lumen and Maksoor keeps its
minimal five-command protocol.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import uuid
from typing import Any, Dict, Optional

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "limpet-remote-cli-mcp"
SERVER_VERSION = "0.1.0"

DEFAULT_SOCKET_PATH = "/run/remote-cli/shared.sock"
SOCKET_PATH = os.environ.get("REMOTE_CLI_SOCKET_PATH", DEFAULT_SOCKET_PATH)

# Per-request socket timeout. Every remote-cli command is a single fast
# round-trip (read a buffer, write to a PTY fd), so this bounds one request —
# it is NOT the budget for a long-running `exec`, which is governed by
# tool_exec's own `timeout` deadline across many such requests. Conflating the
# two is what capped exec at 5s in the first cut (wq#4397 finding 6).
REQUEST_TIMEOUT = 5.0
DEFAULT_TIMEOUT = REQUEST_TIMEOUT  # back-compat alias for the old name

EXEC_POLL_INTERVAL = 0.2
EXEC_DETECT_LINES = 120  # scrollback tail polled for the marker; it lands last


class RemoteCliError(RuntimeError):
    """Raised when the remote-cli Unix socket can't be reached or misbehaves."""


class RemoteCliClient:
    """One-shot request/response client for remote-cli's shared-session
    socket: connect, send a JSON line, read a JSON line back, disconnect.
    remote-cli owns the long-lived PTY; this client never holds the
    connection open between tool calls, so concurrent agent + human access
    to the same session is remote-cli's concern, not this client's."""

    def __init__(self, socket_path: Optional[str] = None, timeout: float = REQUEST_TIMEOUT):
        # Resolved at construction, not at def time, so SOCKET_PATH stays the
        # single source of truth (and is patchable by the live-daemon tests).
        self._socket_path = socket_path or SOCKET_PATH
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

    def request(self, cmd: str, **fields: Any) -> Dict[str, Any]:
        """Issue one `{"cmd": ...}` request. `cmd` must be one of remote-cli's
        five commands (remote-cli:271-292); anything else comes back as
        `{"ok": false, "error": "unknown cmd ..."}` from the daemon."""
        if self._sock is None:
            raise RemoteCliError("not connected; call connect() first")
        payload = json.dumps({"cmd": cmd, **fields}) + "\n"
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


def _call(cmd: str, timeout: float = REQUEST_TIMEOUT, **fields: Any) -> Dict[str, Any]:
    """Open a fresh connection, issue one request, close it. Shared by all
    four tool functions so each is independently retryable. `timeout` is the
    socket timeout actually applied to this round-trip — it is passed through
    to RemoteCliClient rather than being sent as a JSON field the daemon has
    no use for (wq#4397 finding 6: the first cut did the latter, so the
    caller's timeout was silently ignored)."""
    client = RemoteCliClient(timeout=timeout)
    try:
        client.connect()
        return client.request(cmd, **fields)
    except RemoteCliError as exc:
        return {"error": str(exc)}
    finally:
        client.close()


def _exec_payload(command: str, token: str) -> str:
    """The line typed into the shared shell: the command, then a marker line
    carrying its exit status.

    The marker is assembled by printf from separate arguments (`RC`, `DONE`,
    …) so the SHELL'S ECHO of this line cannot itself match the marker regex —
    the echo contains `RC DONE <token>` (spaces, from the argument list) while
    only printf's output contains `RCDONE-<token>-<status>`. Without that
    split, polling would match the echo instantly and report the exit code of
    the previous command.
    """
    one_line = command.rstrip("\r\n")
    return (f"{one_line}; __rc_status=$?; "
            f"printf '%s%s-%s-%s\\n' RC DONE {token} \"$__rc_status\"\n")


def _exec_marker(token: str) -> "re.Pattern[str]":
    return re.compile(r"RCDONE-" + re.escape(token) + r"-(\d+)")


def _exec_output(scrollback: str, token: str) -> str:
    """Carve the command's own output out of raw PTY scrollback.

    `token` appears exactly twice: first in the shell's echo of the payload
    line, then in printf's marker output. Everything between the end of the
    echo line and the start of the marker is what the command printed. Raw
    scrollback keeps the PTY's \\r\\n and any escape sequences the command
    emitted — deliberately not scrubbed, since for bench RE work the escapes
    are sometimes the signal.
    """
    idx = scrollback.find(token)
    if idx != -1:
        nl = scrollback.find("\n", idx)
        scrollback = scrollback[nl + 1:] if nl != -1 else ""
    m = _exec_marker(token).search(scrollback)
    if m:
        scrollback = scrollback[:m.start()]
    return scrollback.rstrip("\r\n")


def _exec_request_timeout(deadline: float) -> float:
    """One round-trip's socket timeout, never outliving the caller's deadline."""
    return max(0.1, min(REQUEST_TIMEOUT, deadline - time.monotonic()))


def tool_exec(command: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Run a command in the shared terminal session and wait for it to finish.
    ENERGIZING — see module docstring: this mutates a PTY a human may be
    watching live.

    Synthesized from `send_keys` + polled `scrollback`, because remote-cli has
    no synchronous-exec primitive (see module docstring). `timeout` is the real
    wall-clock budget for the whole run, honored across every poll.

    Limits, by construction:
      - the shared session must be a POSIX shell sitting at a prompt. If a
        human left it inside vim/less/a REPL, this types the command into THAT
        program instead, and the marker never appears -> a timeout result.
      - `command` is one shell line; a trailing newline is stripped. Embedded
        newlines run as successive lines and `exit_code` is the last one's.
      - `output` is carved out of raw scrollback, so it carries the PTY's
        \\r\\n and whatever escapes the command emitted.
    """
    token = uuid.uuid4().hex[:12]
    marker = _exec_marker(token)
    deadline = time.monotonic() + timeout

    sent = _call("send_keys", timeout=_exec_request_timeout(deadline),
                 text=_exec_payload(command, token))
    if "error" in sent or not sent.get("ok"):
        return sent if "error" in sent else {"ok": False, "error": "send_keys refused",
                                             "response": sent}

    while time.monotonic() < deadline:
        time.sleep(min(EXEC_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
        tail = _call("scrollback", timeout=_exec_request_timeout(deadline),
                     lines=EXEC_DETECT_LINES)
        if "error" in tail:
            return tail
        if not marker.search(tail.get("text", "")):
            continue
        full = _call("scrollback", timeout=_exec_request_timeout(deadline), lines=0)
        text = full.get("text", "") if full.get("ok") else tail.get("text", "")
        m = marker.search(text)
        return {"ok": True, "command": command,
                "exit_code": int(m.group(1)) if m else None,
                "output": _exec_output(text, token)}

    return {"ok": False, "command": command, "timed_out": True,
            "error": f"exec timed out after {timeout}s with no completion marker; "
                     f"the command may still be running in the shared session"}


def tool_snapshot() -> Dict[str, Any]:
    """Read the current screen state of the shared terminal. Read-only."""
    return _call("snapshot")


def tool_send(keys: str) -> Dict[str, Any]:
    """Type raw keystrokes into the shared terminal (no implied newline/command
    framing — use for control sequences, interactive prompts, arrow keys).
    ENERGIZING per the ticket: routed through the Sentinel energize-confirm
    gate before remote-cli ever sees the keys."""
    return _call("send_keys", text=keys)


def tool_logs(lines: int = 200) -> Dict[str, Any]:
    """Read the last `lines` of scrollback from the shared terminal. Read-only."""
    return _call("scrollback", lines=lines)


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
