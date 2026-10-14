"""wq#3808 LIMPET meta-server: thin CLI -> MCP tool wrapper.

Turns a CliToolEntry (registry.py) into something that looks like any other
MCP tool to the meta-server's dispatcher: a name, a description, a JSON-schema
for its arguments, and an invoke(args) -> dict callable.
"""
from __future__ import annotations

import subprocess

from registry import CliToolEntry


class CliToolError(RuntimeError):
    pass


def _render_command(template: list[str], args: dict) -> list[str]:
    """Substitute {arg_name} placeholders in the command template.

    A bare positional placeholder like "{path}" is replaced wholesale (so
    args can contain spaces without needing shell quoting - we never use
    shell=True). Unknown placeholders are a caller error, not silently
    dropped, so a typo'd arg name fails loudly instead of running a truncated
    command.
    """
    rendered = []
    for part in template:
        if part.startswith("{") and part.endswith("}"):
            key = part[1:-1]
            if key not in args:
                raise CliToolError(f"missing required argument '{key}'")
            rendered.append(str(args[key]))
        else:
            rendered.append(part)
    return rendered


class CliTool:
    def __init__(self, entry: CliToolEntry):
        self.entry = entry
        self.name = entry.name
        self.description = entry.description
        self.input_schema = entry.args_schema or {"type": "object", "properties": {}}

    def invoke(self, args: dict) -> dict:
        command = _render_command(self.entry.command, args)
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.entry.timeout_sec,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CliToolError(
                f"'{self.name}' timed out after {self.entry.timeout_sec}s"
            ) from exc
        except FileNotFoundError as exc:
            raise CliToolError(f"'{self.name}' command not found: {command[0]}") from exc
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }


def build_cli_tool(entry: CliToolEntry) -> CliTool:
    return CliTool(entry)
