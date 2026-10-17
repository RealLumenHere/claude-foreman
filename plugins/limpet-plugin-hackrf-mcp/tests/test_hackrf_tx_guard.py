#!/usr/bin/env python3
"""Tests for hackrf_tx_guard.py (wq#4154).

Pure-logic tests exercise is_blocked_tx_call()/tx_refusal() against synthetic
JSON-RPC requests. An integration test runs run_proxy() against a real
subprocess fake upstream (a tiny stdio echo server launched via
`python3 -I -c`) to exercise the actual proxy plumbing -- stdin forwarding,
the background stdout-pump thread, and process teardown -- not just the
filter function in isolation.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))

import hackrf_tx_guard as guard  # noqa: E402


def test_tx_direction_is_blocked():
    request = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "hackrf_transfer", "arguments": {"direction": "tx"}},
    }
    assert guard.is_blocked_tx_call(request)


def test_rx_direction_is_not_blocked():
    request = {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "hackrf_transfer", "arguments": {"direction": "rx"}},
    }
    assert not guard.is_blocked_tx_call(request)


def test_other_tool_is_not_blocked():
    request = {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "hackrf_info", "arguments": {}},
    }
    assert not guard.is_blocked_tx_call(request)


def test_missing_params_is_not_blocked():
    assert not guard.is_blocked_tx_call({"jsonrpc": "2.0", "id": 4, "method": "tools/call"})


def test_non_call_method_is_not_blocked():
    assert not guard.is_blocked_tx_call({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})


def test_tx_refusal_shape_preserves_request_id_and_flags_error():
    refusal = guard.tx_refusal(7)
    assert refusal["id"] == 7
    assert refusal["result"]["isError"] is True
    assert "tx" in refusal["result"]["content"][0]["text"].lower()


# Minimal fake upstream: echoes every request back as a successful result
# carrying the same id, same shape as the real server's tools/call response.
_FAKE_UPSTREAM_SRC = (
    "import json, sys\n"
    "for line in sys.stdin:\n"
    "    line = line.strip()\n"
    "    if not line:\n"
    "        continue\n"
    "    msg = json.loads(line)\n"
    "    reply = {'jsonrpc': '2.0', 'id': msg.get('id'), 'result': "
    "{'content': [{'type': 'text', 'text': 'upstream-reached'}], 'isError': False}}\n"
    "    sys.stdout.write(json.dumps(reply) + '\\n')\n"
    "    sys.stdout.flush()\n"
)


def _spawn_fake_upstream() -> "subprocess.Popen[str]":
    return subprocess.Popen(
        [sys.executable, "-I", "-c", _FAKE_UPSTREAM_SRC],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
    )


def test_run_proxy_blocks_tx_but_forwards_rx_and_other_tools():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "hackrf_transfer", "arguments": {"direction": "tx"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "hackrf_transfer", "arguments": {"direction": "rx"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "hackrf_info", "arguments": {}}},
    ]
    in_stream = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    out_stream = io.StringIO()

    exit_code = guard.run_proxy(in_stream, out_stream, _spawn_fake_upstream)
    assert exit_code == 0

    replies = [json.loads(l) for l in out_stream.getvalue().splitlines() if l.strip()]
    assert len(replies) == 3

    tx_reply = next(r for r in replies if r["id"] == 1)
    assert tx_reply["result"]["isError"] is True
    assert "tx" in tx_reply["result"]["content"][0]["text"].lower()

    rx_reply = next(r for r in replies if r["id"] == 2)
    assert rx_reply["result"]["isError"] is False
    assert rx_reply["result"]["content"][0]["text"] == "upstream-reached"

    info_reply = next(r for r in replies if r["id"] == 3)
    assert info_reply["result"]["isError"] is False
    assert info_reply["result"]["content"][0]["text"] == "upstream-reached"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
