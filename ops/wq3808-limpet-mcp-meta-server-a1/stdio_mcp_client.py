"""wq#3808 LIMPET meta-server: minimal MCP client for proxying to child servers.

Individual tools (REA, Ghidra MCP, radare2 MCP, ...) already speak MCP. This
module is the "proxy" half of the meta-server: it spawns a child MCP server
over stdio, does the initialize handshake, lists its tools, and forwards
tools/call requests to it. It only implements the slice of the MCP spec the
meta-server needs (stdio transport, newline-delimited JSON-RPC) rather than
pulling in the full `mcp` SDK, so it has no dependency beyond the stdlib.

Every read is bounded (wq#3815 bug 3, wq#3821 ruling): the child's stdout is
drained by a reader thread into a queue, so a child that never answers costs
the caller at most `startup_timeout_sec` / `request_timeout_sec` instead of
wedging it forever. A timed-out or malformed child surfaces as
ChildServerError, which is what ToolRegistry.rebuild() already catches to skip
one bad entry and keep serving the rest of the tool set.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time

DEFAULT_STARTUP_TIMEOUT_SEC = 10.0
DEFAULT_REQUEST_TIMEOUT_SEC = 30.0

_EOF = object()  # pushed by the reader thread when the child closes stdout


class ChildServerError(RuntimeError):
    pass


class ChildServerTimeout(ChildServerError):
    """A child server did not answer within its timeout.

    Subclasses ChildServerError so every existing caller (`rebuild()`,
    `MetaServer.handle_request()`) already handles it as a child failure.
    """


class StdioMcpClient:
    """One persistent subprocess connection to a child MCP server."""

    def __init__(
        self,
        name: str,
        command: list[str],
        env: dict[str, str] | None = None,
        startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC,
        request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC,
    ):
        self.name = name
        self.command = command
        self.env = env
        self.startup_timeout_sec = startup_timeout_sec
        self.request_timeout_sec = request_timeout_sec
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._stdout_lines: queue.Queue = queue.Queue()
        self._stderr_chunks: list[str] = []
        self._readers: list[threading.Thread] = []
        self._abandoned_ids: set[int] = set()
        self._saw_eof = False

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
        self._start_readers()
        try:
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
            self._recv(self.startup_timeout_sec, "initialize")
        except (ChildServerError, OSError):
            # A child that hangs or dies during the handshake is never usable;
            # kill it outright rather than leaving an orphan behind for the
            # caller that is about to skip this entry. kill (not stop) because
            # a child ignoring stdin EOF would otherwise cost another wait.
            self._kill()
            raise
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            self._reap(proc)
        self._join_readers()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def list_tools(self) -> list[dict]:
        resp = self._request("tools/list", {})
        return resp.get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        return self._request("tools/call", {"name": tool_name, "arguments": arguments})

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.kill()
        self._reap(proc)
        self._join_readers()

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def _start_readers(self) -> None:
        """Drain both child pipes in daemon threads.

        Reading in a thread is what makes a bounded `_recv()` possible at all:
        the child's stdout is a buffered text stream, so select()-ing on its fd
        can report "not ready" while a complete line already sits in the
        wrapper's buffer. Draining stderr the same way removes a second hang —
        the old `_recv()` error path called a blocking `stderr.read()`, which a
        child that closed stdout but held stderr open never returns from.
        """

        def pump_stdout(stream) -> None:
            for line in stream:
                self._stdout_lines.put(line)
            self._stdout_lines.put(_EOF)

        def pump_stderr(stream) -> None:
            for line in stream:
                self._stderr_chunks.append(line)

        assert self._proc is not None
        for target, stream in (
            (pump_stdout, self._proc.stdout),
            (pump_stderr, self._proc.stderr),
        ):
            if stream is None:
                continue
            thread = threading.Thread(
                target=target, args=(stream,), daemon=True, name=f"limpet-{self.name}-reader"
            )
            thread.start()
            self._readers.append(thread)

    def _join_readers(self) -> None:
        for thread in self._readers:
            thread.join(timeout=1.0)
        self._readers = []

    def _stderr_text(self) -> str:
        return "".join(self._stderr_chunks).strip()

    def _alloc_id(self) -> int:
        current = self._next_id
        self._next_id += 1
        return current

    def _send(self, message: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ChildServerError(f"child server '{self.name}' is not running")
        line = json.dumps(message)
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            # BrokenPipeError on a child that died, ValueError on a closed
            # pipe. Surfacing these as ChildServerError keeps a dead child from
            # escaping handle_request() and taking serve_forever() down with it.
            raise ChildServerError(
                f"child server '{self.name}' stdin is gone: {exc}"
            ) from exc

    def _recv(self, timeout: float, context: str) -> dict:
        if self._proc is None:
            raise ChildServerError(f"child server '{self.name}' is not running")
        deadline = time.monotonic() + timeout
        while True:
            if self._saw_eof:
                raise ChildServerError(
                    f"child server '{self.name}' closed its connection unexpectedly: "
                    f"{self._stderr_text()}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChildServerTimeout(
                    f"child server '{self.name}' did not answer {context} within {timeout}s"
                )
            try:
                item = self._stdout_lines.get(timeout=remaining)
            except queue.Empty:
                raise ChildServerTimeout(
                    f"child server '{self.name}' did not answer {context} within {timeout}s"
                ) from None
            if item is _EOF:
                self._saw_eof = True
                continue
            try:
                message = json.loads(item)
            except ValueError as exc:
                raise ChildServerError(
                    f"child server '{self.name}' wrote non-JSON on stdout "
                    f"while answering {context}: {item.strip()[:200]!r} ({exc})"
                ) from exc
            msg_id = message.get("id")
            if msg_id is not None and msg_id in self._abandoned_ids:
                # Late answer to a request we already timed out on. Drop it, or
                # it would be handed to the *next* request and fail its id check.
                self._abandoned_ids.discard(msg_id)
                continue
            return message

    def _abandon(self, req_id: int, method: str) -> None:
        self._abandoned_ids.add(req_id)
        try:
            # MCP lifecycle: a sender that gives up on a request SHOULD tell the
            # receiver so it can stop work on it.
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": req_id, "reason": f"{method} timed out"},
                }
            )
        except ChildServerError:
            pass

    def _request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        budget = self.request_timeout_sec if timeout is None else timeout
        with self._lock:
            req_id = self._alloc_id()
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            try:
                response = self._recv(budget, method)
            except ChildServerTimeout:
                self._abandon(req_id, method)
                raise
        if response.get("id") != req_id:
            raise ChildServerError(
                f"child server '{self.name}' returned mismatched response id "
                f"(expected {req_id}, got {response.get('id')})"
            )
        if "error" in response:
            raise ChildServerError(f"child server '{self.name}' error: {response['error']}")
        return response.get("result", {})
