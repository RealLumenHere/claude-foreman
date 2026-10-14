"""wq#3808 LIMPET meta-server: minimal MCP client for proxying to child servers.

Individual tools (REA, Ghidra MCP, radare2 MCP, ...) already speak MCP. This
module is the "proxy" half of the meta-server: it spawns a child MCP server
over stdio, does the initialize handshake, lists its tools, and forwards
tools/call requests to it. It only implements the slice of the MCP spec the
meta-server needs (stdio transport, newline-delimited JSON-RPC) rather than
pulling in the full `mcp` SDK, so it has no dependency beyond the stdlib.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading


class ChildServerError(RuntimeError):
    pass


class StdioMcpClient:
    """One persistent subprocess connection to a child MCP server."""

    def __init__(self, name: str, command: list[str], env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 1

    def start(self) -> None:
        merged_env = dict(os.environ)
        if self.env:
            merged_env.update(self.env)
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=merged_env,
        )
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._alloc_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "limpet-meta-server", "version": "0.1"},
                },
            }
        )
        self._recv()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.stdin.close()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def list_tools(self) -> list[dict]:
        resp = self._request("tools/list", {})
        return resp.get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return self._request("tools/call", {"name": tool_name, "arguments": arguments})

    def _alloc_id(self) -> int:
        current = self._next_id
        self._next_id += 1
        return current

    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ChildServerError(f"child server '{self.name}' is not running")
        line = json.dumps(message)
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _recv(self) -> dict:
        if self._proc is None or self._proc.stdout is None:
            raise ChildServerError(f"child server '{self.name}' is not running")
        line = self._proc.stdout.readline()
        if not line:
            stderr = self._proc.stderr.read() if self._proc.stderr else ""
            raise ChildServerError(
                f"child server '{self.name}' closed its connection unexpectedly: {stderr.strip()}"
            )
        return json.loads(line)

    def _request(self, method: str, params: dict) -> dict:
        with self._lock:
            req_id = self._alloc_id()
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            response = self._recv()
        if response.get("id") != req_id:
            raise ChildServerError(
                f"child server '{self.name}' returned mismatched response id "
                f"(expected {req_id}, got {response.get('id')})"
            )
        if "error" in response:
            raise ChildServerError(f"child server '{self.name}' error: {response['error']}")
        return response.get("result", {})
