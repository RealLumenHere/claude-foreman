"""wq#3807 — seed the drop-in directory from the LIMPET tool catalog.

One-time (or re-runnable) import of ``tool-catalog.json`` (the hand-maintained
list of the ~147 RE tools slated for the Limpet node) into one validated
per-tool manifest under tools.d. Going forward each tool re-registers itself on
install via :mod:`register`; this importer bootstraps the catalog that predates
self-registration.

The catalog's own field names are not assumed to match the manifest schema, so
each field is read through an alias list. A tool that still cannot be made valid
(e.g. no runnable path to build a health check from) is reported and skipped,
never guessed into the manifest.
"""

from __future__ import annotations

import json

from . import schema
from .register import register_tool
from .registry import DEFAULT_TOOLS_DIR

DEFAULT_CATALOG = "/home/lumen/projects/limpet/tool-catalog.json"

# catalog key -> manifest field. First alias present on an entry wins.
_ALIASES = {
    "name": ("name", "tool", "id"),
    "path": ("path", "binary", "bin", "executable", "exec"),
    "version": ("version", "ver"),
    "description": ("description", "desc", "summary"),
    "category": ("category", "group", "class"),
    "capabilities": ("capabilities", "caps", "features", "tags"),
    "input_formats": ("input_formats", "inputs", "input"),
    "output_formats": ("output_formats", "outputs", "output"),
    "architectures": ("architectures", "arches", "arch", "archs"),
    "hardware_requirements": ("hardware_requirements", "hardware", "hw", "requirements"),
    "mcp_endpoint": ("mcp_endpoint", "mcp", "mcp_url", "endpoint"),
    "health_check": ("health_check", "healthcheck", "health", "check", "probe"),
}


def _pick(entry: dict, field: str):
    for alias in _ALIASES[field]:
        if alias in entry and entry[alias] not in (None, ""):
            return entry[alias]
    return None


def catalog_to_manifest(entry: dict, *, synth_health_check: bool = True) -> dict:
    """Map one catalog entry onto a manifest dict (unvalidated)."""
    manifest: dict = {}
    for field in _ALIASES:
        value = _pick(entry, field)
        if value is not None:
            manifest[field] = value
    # Many catalog rows predate the health_check field. Derive a conservative
    # default from the path rather than dropping the tool, unless disabled.
    if synth_health_check and not manifest.get("health_check") and manifest.get("path"):
        manifest["health_check"] = f"{manifest['path']} --version"
    return manifest


def load_catalog(catalog_path: str = DEFAULT_CATALOG) -> list[dict]:
    """Read the catalog file. Accepts a bare list or ``{"tools": [...]}``."""
    with open(catalog_path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get("tools", [])
    if not isinstance(data, list):
        raise ValueError(f"{catalog_path}: expected a list of tools or "
                         "{'tools': [...]}, got " + type(data).__name__)
    return data


def import_catalog(catalog_path: str = DEFAULT_CATALOG,
                   tools_dir: str = DEFAULT_TOOLS_DIR, *,
                   synth_health_check: bool = True) -> tuple[list[str], list[str]]:
    """Import every catalog entry into ``tools_dir``.

    Returns ``(written, errors)``: paths written and per-entry problem strings.
    """
    entries = load_catalog(catalog_path)
    written: list[str] = []
    errors: list[str] = []
    for i, entry in enumerate(entries):
        label = entry.get("name") if isinstance(entry, dict) else None
        label = label or f"catalog[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{label}: not a mapping, skipped")
            continue
        manifest = catalog_to_manifest(entry, synth_health_check=synth_health_check)
        problems = schema.validate_tool(manifest, source=label)
        if problems:
            errors.append("; ".join(problems))
            continue
        written.append(register_tool(manifest, tools_dir))
    return written, errors
