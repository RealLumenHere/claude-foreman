"""wq#3807 — build the unified LIMPET tool manifest from the drop-in directory.

Reads every ``*.yaml`` / ``*.yml`` under the tools.d drop-in directory (one file
per tool), validates each against :mod:`schema`, and emits a single
``tools.json`` the agent loads. The unified manifest carries a
``capabilities`` index so the agent can answer "which tool can do X" with one
lookup instead of scanning every entry.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import tempfile

import yaml

from . import schema

DEFAULT_TOOLS_DIR = "/etc/limpet/tools.d"
DEFAULT_MANIFEST = "/etc/limpet/tools.json"
_MANIFEST_SUFFIXES = (".yaml", ".yml")


def load_tool_dir(tools_dir: str) -> tuple[list[dict], list[str]]:
    """Load and validate every per-tool manifest in ``tools_dir``.

    Returns ``(tools, errors)``: normalized tool dicts sorted by name, and a
    list of per-file problem strings. A malformed file never aborts the load —
    it is reported and skipped, so one bad drop-in cannot blind the agent to
    every other installed tool.
    """
    tools: list[dict] = []
    errors: list[str] = []
    if not os.path.isdir(tools_dir):
        return tools, [f"{tools_dir}: drop-in directory does not exist"]

    seen: dict[str, str] = {}
    for entry in sorted(os.listdir(tools_dir)):
        if not entry.endswith(_MANIFEST_SUFFIXES):
            continue
        source = os.path.join(tools_dir, entry)
        try:
            with open(source, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{source}: unreadable ({exc})")
            continue
        try:
            tool = schema.normalize_tool(raw, source=source)
        except schema.ManifestError as exc:
            errors.append(str(exc))
            continue
        if tool["name"] in seen:
            errors.append(f"{source}: duplicate tool name {tool['name']!r} "
                          f"(already defined by {seen[tool['name']]})")
            continue
        seen[tool["name"]] = source
        tools.append(tool)

    tools.sort(key=lambda t: t["name"])
    return tools, errors


def _index(tools: list[dict], field: str) -> dict[str, list[str]]:
    """Invert a list-valued field into ``{value: [tool names]}`` for fast lookup."""
    index: dict[str, list[str]] = {}
    for tool in tools:
        for value in tool.get(field, ()):
            index.setdefault(value, []).append(tool["name"])
    return {k: sorted(v) for k, v in sorted(index.items())}


def build_manifest(tools: list[dict], *, host: str | None = None,
                   generated_at: str | None = None) -> dict:
    """Assemble the unified manifest dict from validated tool entries."""
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "generated_at": generated_at or datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "host": host if host is not None else socket.gethostname(),
        "tool_count": len(tools),
        "tools": {tool["name"]: tool for tool in tools},
        "capabilities": _index(tools, "capabilities"),
        "input_formats": _index(tools, "input_formats"),
        "mcp_endpoints": {t["name"]: t["mcp_endpoint"]
                          for t in tools if t.get("mcp_endpoint")},
    }


def _content_changed(manifest: dict, manifest_path: str) -> bool:
    """True unless ``manifest_path`` already holds the same manifest modulo its
    timestamp. Lets the watcher skip a rewrite when nothing meaningful changed."""
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            existing = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return True
    a = {k: v for k, v in manifest.items() if k != "generated_at"}
    b = {k: v for k, v in existing.items() if k != "generated_at"}
    return a != b


def write_manifest(manifest: dict, manifest_path: str) -> None:
    """Atomically write ``manifest`` as pretty JSON (temp file + rename), so a
    reader never sees a half-written catalog."""
    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    directory = os.path.dirname(manifest_path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tools.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, manifest_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def rebuild(tools_dir: str = DEFAULT_TOOLS_DIR,
            manifest_path: str = DEFAULT_MANIFEST, *,
            force: bool = False, host: str | None = None) -> tuple[dict, list[str]]:
    """Load the drop-in dir, build the unified manifest, and write it if changed.

    Returns ``(manifest, errors)``. Skips the write when the on-disk manifest is
    already current (unless ``force``), keeping the watcher idempotent.
    """
    tools, errors = load_tool_dir(tools_dir)
    manifest = build_manifest(tools, host=host)
    if force or _content_changed(manifest, manifest_path):
        write_manifest(manifest, manifest_path)
    return manifest, errors
