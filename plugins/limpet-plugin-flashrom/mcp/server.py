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


def tool_write(programmer: str, image_path: str, chip: str = "") -> dict:
    """Write an image to flash. REQUIRES energize-confirm gate."""
    if not os.path.isfile(image_path):
        return {"error": f"image not found: {image_path}"}
    args = ["-p", programmer, "-w", image_path]
    if chip:
        args.extend(["-c", chip])
    return _run_flashrom(*args, timeout=300)


def tool_erase(programmer: str, chip: str = "") -> dict:
    """Erase flash chip. REQUIRES energize-confirm gate."""
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
        "description": "Write an image to SPI flash. DESTRUCTIVE — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "programmer": {"type": "string", "description": "Programmer type"},
                "image_path": {"type": "string", "description": "Path to the image file to flash"},
                "chip": {"type": "string", "description": "Chip name (optional)", "default": ""},
            },
            "required": ["programmer", "image_path"]
        },
        "handler": tool_write,
    },
    "flashrom_erase": {
        "description": "Erase SPI flash chip. DESTRUCTIVE — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "programmer": {"type": "string", "description": "Programmer type"},
                "chip": {"type": "string", "description": "Chip name (optional)", "default": ""},
            },
            "required": ["programmer"]
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
