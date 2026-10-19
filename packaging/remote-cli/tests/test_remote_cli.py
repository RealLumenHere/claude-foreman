"""wq#4395: remote-cli's shared-PTY daemon speaks the control-socket protocol
plugins/limpet-plugin-remote-cli/mcp/server.py already consumes (connect,
write one JSON line {"op": ..., ...}, read one JSON line back).

Falsifiability (wq#2630): every assertion below checks for text that only
appears in the response if the daemon actually ran the command against the
live PTY -- a stub that always returned {} or echoed the request back would
fail each of these, not just pass by construction.
"""
import importlib.util
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "remote_cli", Path(__file__).resolve().parent.parent / "lib" / "remote_cli.py")
remote_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(remote_cli)


@pytest.fixture
def running_session(tmp_path):
    """A live SharedSession (/bin/sh) with its control socket served in a
    background thread. Short socket path (tempfile, not pytest's tmp_path)
    to stay under AF_UNIX's ~107-char path limit."""
    socket_dir = tempfile.mkdtemp(prefix="rc-")
    control_path = os.path.join(socket_dir, "ctl.sock")
    session = remote_cli.SharedSession(shell="/bin/sh")
    thread = threading.Thread(
        target=remote_cli.serve_control, args=(session, control_path), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not os.path.exists(control_path) and time.monotonic() < deadline:
        time.sleep(0.01)
    yield session, control_path
    session.close()


def _call(control_path, op, **fields):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(control_path)
    sock.sendall((json.dumps({"op": op, **fields}) + "\n").encode())
    chunks = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        if chunks[-1].endswith(b"\n"):
            break
    sock.close()
    return json.loads(b"".join(chunks).decode())


def test_exec_runs_command_and_returns_its_output(running_session):
    _, control_path = running_session
    marker = "remote-cli-exec-marker-7f3a"
    response = _call(control_path, "exec", command=f"echo {marker}", timeout=5)
    assert marker in response["output"]


def test_exec_of_different_command_returns_different_output(running_session):
    """Falsifiability boundary: a daemon that returned a cached/fixed string
    regardless of input would pass test_exec_runs_command_and_returns_its_output
    alone; this asserts the output actually varies with the command."""
    _, control_path = running_session
    first = _call(control_path, "exec", command="echo first-7f3a", timeout=5)
    second = _call(control_path, "exec", command="echo second-9b1c", timeout=5)
    assert "first-7f3a" in first["output"]
    assert "second-9b1c" not in first["output"]
    assert "second-9b1c" in second["output"]
    assert "first-7f3a" not in second["output"]


def test_send_writes_raw_keys_without_implied_newline(running_session):
    """`send` must NOT append a newline itself: writing a command with no
    trailing "\\n" should leave it unexecuted until a later `send` supplies
    one. The PTY line discipline echoes typed characters immediately (that's
    normal tty behavior, not execution), so the marker appears once from that
    echo either way -- the real signal is a SECOND occurrence, from the
    command's own output, which only shows up once "\\n" is sent. A `send`
    that silently appended its own newline (making it indistinguishable from
    `exec`) would make the second occurrence appear after the first call."""
    _, control_path = running_session
    marker = "remote-cli-send-marker-9a21"
    response = _call(control_path, "send", keys=f"echo {marker}")
    assert response == {"ok": True}
    time.sleep(0.2)
    assert _call(control_path, "logs", lines=50)["logs"].count(marker) == 1
    _call(control_path, "send", keys="\n")
    time.sleep(0.3)
    assert _call(control_path, "logs", lines=50)["logs"].count(marker) == 2


def test_logs_reflects_scrollback_after_exec(running_session):
    _, control_path = running_session
    marker = "remote-cli-logs-marker-2e9d"
    _call(control_path, "exec", command=f"echo {marker}", timeout=5)
    logs = _call(control_path, "logs", lines=50)["logs"]
    assert marker in logs


def test_snapshot_reflects_most_recent_output(running_session):
    _, control_path = running_session
    marker = "remote-cli-snapshot-marker-4c6b"
    _call(control_path, "exec", command=f"echo {marker}", timeout=5)
    screen = _call(control_path, "snapshot")["screen"]
    assert marker in screen


def test_unknown_op_is_reported(running_session):
    _, control_path = running_session
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(control_path)
    sock.sendall((json.dumps({"op": "not-a-real-op"}) + "\n").encode())
    chunk = sock.recv(65536)
    sock.close()
    response = json.loads(chunk.decode())
    assert "error" in response
    assert "not-a-real-op" in response["error"]
