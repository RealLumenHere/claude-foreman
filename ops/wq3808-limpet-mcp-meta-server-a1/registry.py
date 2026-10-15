"""wq#3808 LIMPET meta-server: registry manifest loading + hot-reload.

The manifest is a single JSON file listing two kinds of entries:
  - mcp_servers: tools that already speak MCP on their own (REA, Ghidra MCP,
    radare2 MCP, ...). The meta-server proxies to them.
  - cli_tools: tools with no MCP server of their own. These get a thin CLI
    wrapper (cli_wrapper.py) so they show up as ordinary MCP tools too.

Both kinds are merged into one flat, namespaced tool list by meta_server.py.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from stdio_mcp_client import DEFAULT_REQUEST_TIMEOUT_SEC, DEFAULT_STARTUP_TIMEOUT_SEC


class ManifestError(ValueError):
    """Raised when a manifest file is missing required fields or malformed."""


@dataclass
class McpServerEntry:
    name: str
    transport: str  # "stdio" or "http"
    enabled: bool = True
    command: list[str] | None = None  # required for transport=stdio
    url: str | None = None  # required for transport=http
    env: dict[str, str] = field(default_factory=dict)
    # Read budgets for the proxy half, the mcp_servers counterpart of a
    # cli_tools entry's timeout_sec (wq#3821). Startup is the initialize
    # handshake; request covers tools/list and each tools/call.
    startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC
    request_timeout_sec: float = DEFAULT_REQUEST_TIMEOUT_SEC
    # Provenance stamped on every call's _meta.evidence (wq#3964): the plugin's
    # git sha, and whether its output is target-derived (untrusted) data.
    git_sha: str | None = None
    untrusted_output: bool = True


@dataclass
class CliToolEntry:
    name: str
    command: list[str]
    description: str = ""
    enabled: bool = True
    args_schema: dict = field(default_factory=dict)
    timeout_sec: float = 30.0
    git_sha: str | None = None
    untrusted_output: bool = True


@dataclass
class Manifest:
    mcp_servers: list[McpServerEntry]
    cli_tools: list[CliToolEntry]
    source_path: str
    mtime: float


def _require(entry: dict, key: str, ctx: str):
    if key not in entry:
        raise ManifestError(f"{ctx} missing required field '{key}'")
    return entry[key]


def _positive_timeout(entry: dict, key: str, default: float, ctx: str) -> float:
    """Read a timeout field, rejecting values that can never make progress.

    A zero or negative budget would fail every read before the child could
    possibly answer, which looks exactly like the bug this knob exists to
    prevent — so it is a manifest error, not a silently-accepted setting.
    """
    if key not in entry:
        return default
    try:
        value = float(entry[key])
    except (TypeError, ValueError):
        raise ManifestError(f"mcp_servers entry '{ctx}' has non-numeric '{key}'") from None
    if value <= 0:
        raise ManifestError(f"mcp_servers entry '{ctx}' has '{key}' <= 0 ({value})")
    return value


def _parse_mcp_server(entry: dict) -> McpServerEntry:
    name = _require(entry, "name", "mcp_servers entry")
    transport = entry.get("transport", "stdio")
    if transport not in ("stdio", "http"):
        raise ManifestError(f"mcp_servers entry '{name}' has unknown transport '{transport}'")
    command = entry.get("command")
    url = entry.get("url")
    if transport == "stdio" and not command:
        raise ManifestError(f"mcp_servers entry '{name}' is transport=stdio but has no 'command'")
    if transport == "http" and not url:
        raise ManifestError(f"mcp_servers entry '{name}' is transport=http but has no 'url'")
    return McpServerEntry(
        name=name,
        transport=transport,
        enabled=bool(entry.get("enabled", True)),
        command=list(command) if command else None,
        url=url,
        env=dict(entry.get("env", {})),
        startup_timeout_sec=_positive_timeout(
            entry, "startup_timeout_sec", DEFAULT_STARTUP_TIMEOUT_SEC, name
        ),
        request_timeout_sec=_positive_timeout(
            entry, "request_timeout_sec", DEFAULT_REQUEST_TIMEOUT_SEC, name
        ),
        git_sha=entry.get("git_sha"),
        untrusted_output=bool(entry.get("untrusted_output", True)),
    )


def _parse_cli_tool(entry: dict) -> CliToolEntry:
    name = _require(entry, "name", "cli_tools entry")
    command = _require(entry, "command", f"cli_tools entry '{name}'")
    return CliToolEntry(
        name=name,
        command=list(command),
        description=entry.get("description", ""),
        enabled=bool(entry.get("enabled", True)),
        args_schema=dict(entry.get("args_schema", {})),
        timeout_sec=float(entry.get("timeout_sec", 30.0)),
        git_sha=entry.get("git_sha"),
        untrusted_output=bool(entry.get("untrusted_output", True)),
    )


def load_manifest(path: str) -> Manifest:
    """Parse the registry manifest at `path`. Raises ManifestError on bad shape."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ManifestError("manifest root must be a JSON object")

    mcp_servers = [_parse_mcp_server(e) for e in raw.get("mcp_servers", [])]
    cli_tools = [_parse_cli_tool(e) for e in raw.get("cli_tools", [])]

    seen = set()
    for item in (*mcp_servers, *cli_tools):
        if item.name in seen:
            raise ManifestError(f"duplicate tool-server name '{item.name}' in manifest")
        seen.add(item.name)

    return Manifest(
        mcp_servers=mcp_servers,
        cli_tools=cli_tools,
        source_path=path,
        mtime=os.stat(path).st_mtime,
    )


class ManifestWatcher:
    """Polls a manifest file's mtime so the meta-server can hot-reload it.

    Hot-reload here means "notice the file changed and hand back a freshly
    parsed Manifest" — meta_server.py is responsible for diffing the old and
    new tool sets and starting/stopping child processes accordingly.
    """

    def __init__(self, path: str):
        self.path = path
        self.manifest = load_manifest(path)

    def poll(self) -> Manifest | None:
        """Return a reloaded Manifest if the file changed on disk, else None."""
        try:
            current_mtime = os.stat(self.path).st_mtime
        except FileNotFoundError:
            return None
        if current_mtime == self.manifest.mtime:
            return None
        new_manifest = load_manifest(self.path)
        self.manifest = new_manifest
        return new_manifest
