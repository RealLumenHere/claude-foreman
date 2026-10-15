"""wq#3935 — Rockchip MCP tool: [[wrappers]] TOML -> meta-server cli_tools JSON.

`rockchip-mcp.toml` (same directory) is the authorable source: one [[wrappers]]
table per rkdeveloptool verb. The LIMPET MCP meta-server
(ops/wq3808-limpet-mcp-meta-server-a1) consumes a JSON registry manifest, so
this module validates the TOML and renders its wrappers into `cli_tools`
entries in exactly the shape the meta-server's registry.py parses. Merging the
rendered array into the deployed manifest's "cli_tools" list is the install
step (the meta-server hot-reloads the manifest file, no restart needed).

Pure parse/validate/render on dicts — no subprocess, no USB. The commands only
ever run inside the meta-server's cli_wrapper.py (shell=False argv, wholesale
{placeholder} substitution), which is why validation here pins the two
properties that substitution relies on: every placeholder is a whole argv
element, and every placeholder is declared (and required) in args_schema.

Stdlib only, same as the meta-server itself (its install target is Limpet,
where no venv is guaranteed — ops/wq3808.../README.md).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import tomllib

MANIFEST_PATH = pathlib.Path(__file__).parent / "rockchip-mcp.toml"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
# A placeholder argv element: the WHOLE element is {name}. An embedded form
# like "--out={path}" is rejected — cli_wrapper.py only substitutes wholesale.
_PLACEHOLDER_RE = re.compile(r"^\{([a-z0-9_]+)\}$")


def load_manifest(path: pathlib.Path = MANIFEST_PATH) -> dict:
    """Parse the TOML manifest. Raises on unreadable/malformed input; semantic
    problems are validate_manifest()'s job so they can all be reported at once."""
    with path.open("rb") as f:
        return tomllib.load(f)


def validate_manifest(data: dict) -> list[str]:
    """Return every problem with a parsed manifest dict; empty list means valid."""
    errors = []
    tool = data.get("tool")
    if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
        errors.append("missing [tool] table with a string `name`")
    wrappers = data.get("wrappers")
    if not isinstance(wrappers, list) or not wrappers:
        return errors + ["missing [[wrappers]] tables"]

    seen = set()
    for i, w in enumerate(wrappers):
        ctx = f"wrappers[{i}]"
        name = w.get("name")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            errors.append(f"{ctx}.name {name!r} must match {_NAME_RE.pattern}")
        elif name in seen:
            errors.append(f"{ctx}.name {name!r} duplicates an earlier wrapper")
        else:
            seen.add(name)
            ctx = f"wrappers[{i}] ({name})"

        command = w.get("command")
        if not isinstance(command, list) or not command or not all(
                isinstance(part, str) and part for part in command):
            errors.append(f"{ctx}.command must be a non-empty list of non-empty strings")
            command = []
        if not isinstance(w.get("description"), str) or not w.get("description", "").strip():
            errors.append(f"{ctx}.description must be a non-empty string")
        if not isinstance(w.get("destructive"), bool):
            errors.append(f"{ctx}.destructive must be set explicitly (true/false)")
        timeout = w.get("timeout_sec")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            errors.append(f"{ctx}.timeout_sec must be a positive number")

        schema = w.get("args_schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            errors.append(f'{ctx}.args_schema must be a JSON-schema object table (type = "object")')
            schema = {}
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        placeholders = set()
        for part in command:
            if _PLACEHOLDER_RE.match(part):
                placeholders.add(_PLACEHOLDER_RE.match(part).group(1))
            elif "{" in part or "}" in part:
                errors.append(
                    f"{ctx}.command element {part!r} embeds a brace: placeholders "
                    "must be whole argv elements (cli_wrapper.py substitutes wholesale)")
        for ph in sorted(placeholders):
            if ph not in properties:
                errors.append(f"{ctx}: placeholder {{{ph}}} is not declared in args_schema.properties")
            if ph not in required:
                errors.append(f"{ctx}: placeholder {{{ph}}} must be listed in args_schema.required")
        for req in required:
            if req not in placeholders:
                errors.append(f"{ctx}: required arg {req!r} is never used as a command placeholder")
    return errors


def render_cli_tools(data: dict) -> list[dict]:
    """Render a validated manifest's wrappers as meta-server cli_tools entries.

    Names are prefixed "<tool>_" so they stay unique inside the meta-server's
    shared "cli." namespace. Destructive wrappers render with enabled=false —
    turning an irreversible write on is a deliberate manifest edit, never a
    default (DESIGN-DECISIONS.md D3).
    """
    errors = validate_manifest(data)
    if errors:
        raise ValueError("; ".join(errors))
    tool_name = data["tool"]["name"]
    entries = []
    for w in data["wrappers"]:
        entry = {
            "name": f"{tool_name}_{w['name']}",
            "command": list(w["command"]),
            "description": w["description"],
            "args_schema": w["args_schema"],
            "enabled": not w["destructive"],
            "timeout_sec": w["timeout_sec"],
        }
        if w["destructive"]:
            entry["note"] = (
                "destructive wrapper rendered disabled by default (wq#3935); "
                "flip enabled only after a verified backup exists")
        entries.append(entry)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd in ("validate", "render"):
        p = sub.add_parser(cmd)
        p.add_argument("--manifest", type=pathlib.Path, default=MANIFEST_PATH)
    args = parser.parse_args(argv)

    data = load_manifest(args.manifest)
    errors = validate_manifest(data)
    if errors:
        for e in errors:
            print(f"INVALID: {e}", file=sys.stderr)
        return 1
    if args.command == "render":
        print(json.dumps(render_cli_tools(data), indent=2))
    else:
        print(f"OK: {len(data['wrappers'])} wrappers valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
