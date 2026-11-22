#!/usr/bin/env python3
"""limpet-flashrom-mcp — Thin MCP server wrapping the flashrom CLI.

wq#4141: no flashrom-mcp exists anywhere. This provides probe/read/write/erase/verify
as MCP tools. Core tier — Limpet ships flashrom and owns both ends.

Each tool maps to one flashrom invocation with structured output parsing.
The server runs as stdio transport (stdin/stdout JSON-RPC).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

# MCP protocol version
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "limpet-flashrom-mcp"
SERVER_VERSION = "0.1.0"

FLASHROM_BIN = os.environ.get("FLASHROM_BIN", "/usr/sbin/flashrom")

# wq#4747 (hal-spec.md §9.2a): the "REQUIRES energize-confirm gate" docstrings below
# used to enforce nothing -- tool_write/tool_erase ran unconditionally. Shelling out to
# energize_confirm.py (not importing it) matches the cross-ops-bundle subprocess pattern
# ops/wq3882-limpet-flashrom-mcp-a1/flashrom_ops.py uses for hal.py, so this server stays
# runnable on a host without the rest of the harness tree.
ENERGIZE_CONFIRM_SCRIPT = os.environ.get(
    "ENERGIZE_CONFIRM_SCRIPT", "/home/lumen/foreman/harness/energize_confirm.py")


class ConfirmRequiredError(RuntimeError):
    """Raised when an energizing tool is called without a valid confirm token."""


def _verify_confirm_token(capability: str, confirm_token: str) -> bool:
    """Shell out to energize_confirm.py's verifier. Split out so tests can mock
    this one call without faking every subprocess.run this server makes."""
    try:
        rc = subprocess.run(
            [sys.executable, ENERGIZE_CONFIRM_SCRIPT, "verify", capability, confirm_token],
            capture_output=True, timeout=10,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return False  # verifier unreachable -- fail closed, never fail open
    return rc == 0


def _require_confirm_token(capability: str, confirm_token: str) -> None:
    """Refuse `capability` unless `confirm_token` was minted by an operator
    (energize_confirm.issue_token, which needs Frank's passphrase, wq#5216). A bool the
    calling model passes does not count."""
    if not confirm_token:
        raise ConfirmRequiredError(
            f"{capability} refused: missing confirm_token (operator-issued -- run "
            f"'python3 {ENERGIZE_CONFIRM_SCRIPT} issue {capability}' from an "
            "interactive terminal, not from this agent session)"
        )
    if not _verify_confirm_token(capability, confirm_token):
        raise ConfirmRequiredError(f"{capability} refused: invalid or expired confirm_token")


def _run_flashrom(*args, timeout=30) -> dict:
    """Run flashrom with args, return structured result."""
    cmd = [FLASHROM_BIN] + list(args)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "error": f"timeout after {timeout}s", "command": " ".join(cmd)}
    except FileNotFoundError:
        return {"exit_code": -1, "error": f"flashrom not found at {FLASHROM_BIN}"}


def tool_probe(programmer: str = "auto") -> dict:
    """Probe for flash chips. Returns chip info if found."""
    args = ["-p", programmer] if programmer != "auto" else []
    result = _run_flashrom(*args)
    return {
        "chips_found": "Found" in result.get("stdout", "") or "Found" in result.get("stderr", ""),
        "raw_output": result,
    }


def tool_read(programmer: str, chip: str = "", output_path: str = "") -> dict:
    """Read flash contents to a file. ALWAYS read before writing."""
    if not output_path:
        output_path = tempfile.mktemp(suffix=".bin", prefix="flashrom-read-")
    args = ["-p", programmer, "-r", output_path]
    if chip:
        args.extend(["-c", chip])
    result = _run_flashrom(*args, timeout=120)
    result["output_file"] = output_path
    if result["exit_code"] == 0:
        result["file_size"] = os.path.getsize(output_path)
    return result


def tool_write(programmer: str, image_path: str, confirm_token: str = "", chip: str = "") -> dict:
    """Write an image to flash. Energizing (hal-spec.md §9.2a): enforces the
    energize-confirm gate via `confirm_token`, not a model-supplied bool."""
    _require_confirm_token("flashrom.write_flash", confirm_token)
    if not os.path.isfile(image_path):
        return {"error": f"image not found: {image_path}"}
    args = ["-p", programmer, "-w", image_path]
    if chip:
        args.extend(["-c", chip])
    return _run_flashrom(*args, timeout=300)


def tool_erase(programmer: str, confirm_token: str = "", chip: str = "") -> dict:
    """Erase flash chip. Energizing (hal-spec.md §9.2a): enforces the
    energize-confirm gate via `confirm_token`, not a model-supplied bool."""
    _require_confirm_token("flashrom.erase_chip", confirm_token)
    args = ["-p", programmer, "-E"]
    if chip:
        args.extend(["-c", chip])
    return _run_flashrom(*args, timeout=120)


def tool_verify(programmer: str, image_path: str, chip: str = "") -> dict:
    """Verify flash contents match an image file."""
    if not os.path.isfile(image_path):
        return {"error": f"image not found: {image_path}"}
    args = ["-p", programmer, "-v", image_path]
    if chip:
        args.extend(["-c", chip])
    result = _run_flashrom(*args, timeout=120)
    result["verified"] = result.get("exit_code") == 0
    return result


TOOLS = {
    "flashrom_probe": {
        "description": "Probe for SPI flash chips on a programmer. Returns chip info if found.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "programmer": {"type": "string", "description": "Programmer type (e.g. ch341a_spi, serprog:dev=/dev/ttyACM0:115200, internal)", "default": "auto"}
            }
        },
        "handler": tool_probe,
    },
    "flashrom_read": {
        "description": "Read flash chip contents to a file. Always read before writing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "programmer": {"type": "string", "description": "Programmer type"},
                "chip": {"type": "string", "description": "Chip name (optional, auto-detected if omitted)", "default": ""},
                "output_path": {"type": "string", "description": "Output file path", "default": ""},
            },
            "required": ["programmer"]
        },
        "handler": tool_read,
    },
    "flashrom_write": {
        "description": "Write an image to SPI flash. DESTRUCTIVE — requires an "
                        "operator-issued confirm_token (energize_confirm.py issue "
                        "flashrom.write_flash); a bool does not count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "programmer": {"type": "string", "description": "Programmer type"},
                "image_path": {"type": "string", "description": "Path to the image file to flash"},
                "confirm_token": {"type": "string", "description": "Operator-issued energize-confirm token"},
                "chip": {"type": "string", "description": "Chip name (optional)", "default": ""},
            },
            "required": ["programmer", "image_path", "confirm_token"]
        },
        "handler": tool_write,
    },
    "flashrom_erase": {
        "description": "Erase SPI flash chip. DESTRUCTIVE — requires an operator-issued "
                        "confirm_token (energize_confirm.py issue flashrom.erase_chip); "
                        "a bool does not count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "programmer": {"type": "string", "description": "Programmer type"},
                "confirm_token": {"type": "string", "description": "Operator-issued energize-confirm token"},
                "chip": {"type": "string", "description": "Chip name (optional)", "default": ""},
            },
            "required": ["programmer", "confirm_token"]
        },
        "handler": tool_erase,
    },
    "flashrom_verify": {
        "description": "Verify flash contents match an image file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "programmer": {"type": "string", "description": "Programmer type"},
                "image_path": {"type": "string", "description": "Path to the image to verify against"},
                "chip": {"type": "string", "description": "Chip name (optional)", "default": ""},
            },
            "required": ["programmer", "image_path"]
        },
        "handler": tool_verify,
    },
}


def handle_request(request: dict) -> dict:
    """Handle one JSON-RPC request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

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
        tool_args = params.get("arguments", {})
        if tool_name not in TOOLS:
            return {"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32601, "message": f"unknown tool: {tool_name}"}}
        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True}}
    elif method == "notifications/initialized":
        return None  # notification, no response
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32601, "message": f"method not found: {method}"}}


def main():
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
