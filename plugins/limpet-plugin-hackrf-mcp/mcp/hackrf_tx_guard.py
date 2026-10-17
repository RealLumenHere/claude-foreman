#!/usr/bin/env python3
"""hackrf_tx_guard — stdio MCP proxy that refuses hackrf_transfer direction="tx".

wq#4154: thehappydinoa/hackrf-mcp's hackrf_transfer tool accepts
direction="rx"|"tx". Upstream annotates it CONFIG (destructiveHint=False) and
relies only on its docstring to warn about transmit (server.py:160,188-195,
per reports/wq4145_limpet_verify_hackrf_mcp_eval_buspirate_mcp.md section 1
finding 1). RF transmission is a risk class beyond needs_energize (regulated,
affects third parties, unbounded with repeat_tx) and its policy is wq#4147's
decision, not this plugin's — so an energize gate on the tool is not enough.

This script sits between the MCP client and the real `hackrf-mcp` process on
stdio. It inspects every `tools/call` request; a call to `hackrf_transfer`
with `arguments.direction == "tx"` is answered locally with an error result
and never reaches upstream. Every other line (including non-JSON, which
upstream's own line framing would reject anyway) is forwarded unmodified in
both directions. A background thread pumps upstream's stdout independently of
the stdin-forwarding loop so a request that gets no reply (e.g. the
`notifications/initialized` notification) cannot stall the proxy.

Stdlib-only on purpose, same rationale as classify_access.py in
limpet-plugin-openocd-mcp: unit-testable without the `mcp` SDK installed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from typing import Any, Callable, Dict, IO

REAL_COMMAND = os.environ.get("LIMPET_HACKRF_MCP_REAL_COMMAND", "hackrf-mcp")
GUARDED_TOOL = "hackrf_transfer"
GUARDED_DIRECTION = "tx"


def tx_refusal(request_id: Any) -> Dict[str, Any]:
    """The JSON-RPC response sent back in place of a refused tx call."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "content": [{
                "type": "text",
                "text": (
                    'refused by limpet-plugin-hackrf-mcp: hackrf_transfer '
                    'direction="tx" is blocked pending wq#4147\'s SDR TX '
                    "policy decision. This plugin wraps hackrf-mcp RX-only v1."
                ),
            }],
            "isError": True,
        },
    }


def is_blocked_tx_call(request: Dict[str, Any]) -> bool:
    """True if `request` is a tools/call for hackrf_transfer with direction="tx"."""
    if request.get("method") != "tools/call":
        return False
    params = request.get("params") or {}
    if params.get("name") != GUARDED_TOOL:
        return False
    arguments = params.get("arguments") or {}
    return arguments.get("direction") == GUARDED_DIRECTION


def _pump_upstream_replies(upstream_stdout: IO[str], out_stream: IO[str]) -> None:
    for line in upstream_stdout:
        out_stream.write(line)
        out_stream.flush()


def run_proxy(
    in_stream: IO[str],
    out_stream: IO[str],
    spawn_upstream: Callable[[], "subprocess.Popen[str]"],
) -> int:
    """Core proxy loop. `spawn_upstream` is injectable so tests can point it
    at a fake upstream process instead of the real hackrf-mcp binary."""
    upstream = spawn_upstream()
    reader = threading.Thread(
        target=_pump_upstream_replies, args=(upstream.stdout, out_stream), daemon=True,
    )
    reader.start()
    try:
        for line in in_stream:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                request = json.loads(stripped)
            except json.JSONDecodeError:
                upstream.stdin.write(line)
                upstream.stdin.flush()
                continue
            if is_blocked_tx_call(request):
                out_stream.write(json.dumps(tx_refusal(request.get("id"))) + "\n")
                out_stream.flush()
                continue
            upstream.stdin.write(line)
            upstream.stdin.flush()
    finally:
        upstream.stdin.close()
        upstream.wait(timeout=5)
        reader.join(timeout=5)
    return upstream.returncode or 0


def _spawn_real_upstream() -> "subprocess.Popen[str]":
    return subprocess.Popen(
        [REAL_COMMAND],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def main() -> int:
    return run_proxy(sys.stdin, sys.stdout, _spawn_real_upstream)


if __name__ == "__main__":
    sys.exit(main())
