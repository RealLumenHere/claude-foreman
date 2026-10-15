"""wq#3808 LIMPET: unified MCP meta-server.

Aggregates every tool the fleet has into ONE MCP endpoint:
  - Tools that already have their own MCP server (REA, Ghidra MCP, radare2
    MCP, ...) are discovered dynamically (tools/list) and proxied through
    (stdio_mcp_client.py).
  - Tools with no MCP server get a thin CLI wrapper (cli_wrapper.py) that
    makes them look like ordinary MCP tools.

An agent talks to this one process over stdio; it never needs to know how
many child servers or CLI tools are actually behind it. The tool set is
driven by a JSON registry manifest (registry.py) and is hot-reloaded: editing
the manifest and sending SIGHUP (or just waiting out the poll interval) adds
or removes tools without restarting the agent's session.

Tool names are namespaced as "<server-or-tool-group>.<tool>" so two child
servers that both happen to export e.g. "list_files" don't collide.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading

from cli_wrapper import CliToolError, build_cli_tool
from evidence import EvidenceLog, attach_to_result, build_evidence, utc_now
from registry import ManifestWatcher
from stdio_mcp_client import ChildServerError, StdioMcpClient

PROTOCOL_VERSION = "2025-06-18"


def namespaced(group: str, tool_name: str) -> str:
    return f"{group}.{tool_name}"


def _entry_matches_client(entry, client: StdioMcpClient) -> bool:
    """Is a live client still the process this manifest entry asks for?

    Only `command` and `env` are compared: those are baked into the running
    subprocess, so changing either means the live child is the wrong process
    and has to be replaced (wq#3815 bug 1 — the old code checked liveness
    only, so a manifest pointing a tool at a fixed binary kept running the
    stale one). Timeouts are plain client attributes and are updated in place.
    """
    return list(entry.command or []) == list(client.command or []) and dict(
        entry.env or {}
    ) == dict(client.env or {})


class ToolRegistry:
    """The merged, namespaced set of tools currently being served.

    Each entry maps a namespaced tool name to (description, input_schema,
    invoke callable). invoke() takes the call's `arguments` dict and returns
    an MCP-shaped result dict.
    """

    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._clients: dict[str, StdioMcpClient] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._tools)

    def rebuild(self, manifest, log=lambda *_: None) -> None:
        """Stop stale child servers, start new ones, recompute the tool set.

        This is the hot-reload path: `manifest` is whatever
        ManifestWatcher.poll() just handed back.
        """
        new_clients: dict[str, StdioMcpClient] = {}
        new_tools: dict[str, dict] = {}

        for entry in manifest.cli_tools:
            if not entry.enabled:
                continue
            tool = build_cli_tool(entry)
            name = namespaced("cli", tool.name)
            new_tools[name] = {
                "description": tool.description,
                "input_schema": tool.input_schema,
                "invoke": tool.invoke,
                "source": _source("cli_tool", "cli", tool.name, entry),
            }

        for entry in manifest.mcp_servers:
            if not entry.enabled:
                continue
            if entry.transport != "stdio":
                log(f"skipping '{entry.name}': transport '{entry.transport}' not yet supported")
                continue
            existing = self._clients.get(entry.name)
            if existing is not None and not _entry_matches_client(entry, existing):
                log(f"child server '{entry.name}': command/env changed, restarting it")
                existing.stop()
                existing = None
            elif existing is not None and not existing.is_alive():
                existing.stop()  # reap the dead process before replacing it
                existing = None
            if existing is not None:
                client = existing
                client.startup_timeout_sec = entry.startup_timeout_sec
                client.request_timeout_sec = entry.request_timeout_sec
            else:
                client = StdioMcpClient(
                    entry.name,
                    entry.command,
                    entry.env,
                    startup_timeout_sec=entry.startup_timeout_sec,
                    request_timeout_sec=entry.request_timeout_sec,
                )
                try:
                    client.start()
                except (ChildServerError, OSError) as exc:
                    # Bounded by entry.startup_timeout_sec, so one bad child
                    # costs this entry and nothing else: every other child and
                    # every cli_tools entry still gets built and served.
                    log(f"failed to start child server '{entry.name}': {exc}")
                    continue
            try:
                child_tools = client.list_tools()
            except ChildServerError as exc:
                log(f"failed to list tools from '{entry.name}': {exc}")
                continue
            new_clients[entry.name] = client
            for child_tool in child_tools:
                name = namespaced(entry.name, child_tool["name"])
                new_tools[name] = {
                    "description": child_tool.get("description", ""),
                    "input_schema": child_tool.get("inputSchema", {"type": "object"}),
                    "invoke": _make_proxy_invoke(client, child_tool["name"]),
                    "source": _source("mcp_server", entry.name, child_tool["name"], entry),
                }

        stale = set(self._clients) - set(new_clients)
        for name in stale:
            self._clients[name].stop()

        with self._lock:
            self._tools = new_tools
            self._clients = new_clients

    def get(self, name: str) -> dict:
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            raise KeyError(name)
        return tool

    def call(self, name: str, arguments: dict) -> dict:
        return self.get(name)["invoke"](arguments)


def _source(kind: str, server: str, child_tool: str, entry) -> dict:
    return {
        "kind": kind,
        "server": server,
        "tool": child_tool,
        "git_sha": entry.git_sha,
        "untrusted_output": entry.untrusted_output,
    }


def _make_proxy_invoke(client: StdioMcpClient, child_tool_name: str):
    def invoke(arguments: dict) -> dict:
        return client.call_tool(child_tool_name, arguments)

    return invoke


class MetaServer:
    """Speaks MCP (stdio, newline-delimited JSON-RPC) upstream to the agent."""

    def __init__(self, manifest_path: str, reload_poll_sec: float = 2.0, workspace: str | None = None):
        self.watcher = ManifestWatcher(manifest_path)
        self.registry = ToolRegistry()
        self.reload_poll_sec = reload_poll_sec
        self._reload_requested = threading.Event()
        workspace = workspace or os.environ.get("LIMPET_WORKSPACE")
        self.evidence_log = EvidenceLog(workspace) if workspace else None
        self.registry.rebuild(self.watcher.manifest, log=self._log)

    def _log(self, message: str) -> None:
        print(f"[limpet-meta] {message}", file=sys.stderr, flush=True)

    def _record(self, block: dict) -> dict:
        """Append a call's evidence to the bound workspace's log, if any.

        A failed append is logged, not raised: losing the log line must not
        also lose the agent's tool result.
        """
        if self.evidence_log is not None:
            try:
                self.evidence_log.append(block)
            except OSError as exc:
                self._log(f"evidence log append failed ({self.evidence_log.path}): {exc}")
        return block

    def request_reload(self) -> None:
        """Hook for SIGHUP: run the manifest check now instead of next tick.

        Wakes `_reload_loop()` immediately. If the manifest is unchanged the
        check finds nothing to do — this forces the check, it does not force a
        rebuild.
        """
        self._reload_requested.set()

    def _reload_loop(self) -> None:
        while True:
            # wait(), not sleep(): a SIGHUP between ticks has to cut the wait
            # short, otherwise request_reload() is inert and an operator waits
            # out the full poll interval (wq#3815 bug 2).
            self._reload_requested.wait(timeout=self.reload_poll_sec)
            self._reload_requested.clear()
            new_manifest = self.watcher.poll()
            if new_manifest is not None:
                self._log(f"manifest changed, reloading from {self.watcher.path}")
                self.registry.rebuild(new_manifest, log=self._log)

    def start_hot_reload(self) -> threading.Thread:
        thread = threading.Thread(target=self._reload_loop, daemon=True)
        thread.start()
        return thread

    def handle_request(self, message: dict) -> dict | None:
        method = message.get("method")
        req_id = message.get("id")

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "limpet-meta-server", "version": "0.1"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            tools = [
                {"name": name, "description": spec["description"], "inputSchema": spec["input_schema"]}
                for name, spec in self.registry.snapshot().items()
            ]
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
        if method == "tools/call":
            params = message.get("params", {})
            name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                tool = self.registry.get(name)
            except KeyError:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"unknown tool '{name}'"},
                }
            started_at = utc_now()
            try:
                result = tool["invoke"](arguments)
            except (CliToolError, ChildServerError) as exc:
                block = self._record(
                    build_evidence(name, tool["source"], arguments, started_at, utc_now(), error=str(exc))
                )
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(exc), "data": {"_meta": {"evidence": block}}},
                }
            block = self._record(
                build_evidence(name, tool["source"], arguments, started_at, utc_now(), result=result)
            )
            return {"jsonrpc": "2.0", "id": req_id, "result": attach_to_result(result, block)}

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"unknown method '{method}'"},
        }

    def serve_forever(self) -> None:
        self.start_hot_reload()
        signal.signal(signal.SIGHUP, lambda *_: self.request_reload())
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            response = self.handle_request(message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: meta_server.py <manifest.json>", file=sys.stderr)
        sys.exit(2)
    server = MetaServer(sys.argv[1])
    server.serve_forever()


if __name__ == "__main__":
    main()
