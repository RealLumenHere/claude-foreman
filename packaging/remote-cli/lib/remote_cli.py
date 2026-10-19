#!/usr/bin/env python3
"""remote-cli: a pure-Python shared-PTY terminal (wq#4395).

Frank and an agent attach to the SAME PTY session this daemon owns -- no
tmux/screen dependency, no copy-paste/screenshot round trip for bench RE
work (design/wq3806_limpet_architecture.md's need; wq#4396 already shipped
an MCP wrapper, plugins/limpet-plugin-remote-cli, that expects exactly the
wire protocol implemented here).

Two Unix domain sockets, stdlib-only:
  - control socket (DEFAULT_CONTROL_SOCKET): one-shot JSON-line request/
    response, one connection per call. Request: {"op": ..., ...fields}.
    Response: a JSON object (shape is per-op; see handle_control_op).
    Ops: exec, snapshot, send, logs.
  - attach socket (DEFAULT_ATTACH_SOCKET): raw byte passthrough. Each
    connected client's bytes are written straight to the PTY master, and
    every byte the PTY produces is mirrored to every attached client. This
    is what `remote-cli attach` (Frank's human terminal) uses, and it is
    exactly the same PTY the control socket's `exec`/`send`/`snapshot`/
    `logs` ops observe -- one shared session, two ways in.

No terminal emulation: `snapshot` returns the tail of the raw output
stream (a reasonable approximation of "what's currently on screen" for
line-oriented bench-RE shell work), not a cursor-addressed screen buffer.
"""
from __future__ import annotations

import argparse
import json
import os
import pty
import select
import socket
import sys
import termios
import threading
import time
import tty
from typing import Dict, List, Optional

DEFAULT_CONTROL_SOCKET = "/run/remote-cli/shared.sock"
DEFAULT_ATTACH_SOCKET = "/run/remote-cli/attach.sock"
MAX_SCROLLBACK = 1 << 20  # 1 MiB
READ_CHUNK = 4096
EXEC_QUIET_SECONDS = 0.3
EXEC_POLL_INTERVAL = 0.05


class SharedSession:
    """Owns the PTY master fd + child shell, a capped scrollback buffer, and
    the set of raw-attach clients currently mirroring its output."""

    def __init__(self, shell: Optional[str] = None):
        self.shell = shell or os.environ.get("SHELL", "/bin/bash")
        self._lock = threading.Lock()
        self._scrollback = bytearray()
        self._attached: List[socket.socket] = []
        self.pid, self.master_fd = pty.fork()
        if self.pid == 0:
            os.execvp(self.shell, [self.shell])  # child never returns
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        while True:
            try:
                data = os.read(self.master_fd, READ_CHUNK)
            except OSError:
                return
            if not data:
                return
            with self._lock:
                self._scrollback.extend(data)
                overflow = len(self._scrollback) - MAX_SCROLLBACK
                if overflow > 0:
                    del self._scrollback[:overflow]
                dead = []
                for client in self._attached:
                    try:
                        client.sendall(data)
                    except OSError:
                        dead.append(client)
                for client in dead:
                    self._attached.remove(client)

    def attach(self, client: socket.socket) -> None:
        with self._lock:
            self._attached.append(client)

    def detach(self, client: socket.socket) -> None:
        with self._lock:
            if client in self._attached:
                self._attached.remove(client)

    def write(self, data: bytes) -> None:
        os.write(self.master_fd, data)

    def scrollback_len(self) -> int:
        with self._lock:
            return len(self._scrollback)

    def snapshot(self, nbytes: int = 4096) -> str:
        with self._lock:
            tail = bytes(self._scrollback[-nbytes:])
        return tail.decode(errors="replace")

    def logs(self, lines: int = 200) -> str:
        with self._lock:
            text = bytes(self._scrollback).decode(errors="replace")
        return "\n".join(text.splitlines()[-lines:])

    def exec_command(self, command: str, timeout: float = 30.0) -> str:
        """Write `command` + a newline, then poll the scrollback until it
        stops growing (quiescence) or `timeout` elapses, and return only the
        bytes that arrived after the write -- this command's own output."""
        start = self.scrollback_len()
        self.write(command.encode() + b"\n")
        deadline = time.monotonic() + timeout
        last_len = start
        last_growth = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(EXEC_POLL_INTERVAL)
            cur_len = self.scrollback_len()
            if cur_len > last_len:
                last_len = cur_len
                last_growth = time.monotonic()
            elif cur_len > start and time.monotonic() - last_growth > EXEC_QUIET_SECONDS:
                break
        with self._lock:
            tail = bytes(self._scrollback[start:])
        return tail.decode(errors="replace")

    def close(self) -> None:
        try:
            os.close(self.master_fd)
        except OSError:
            pass


def handle_control_op(session: SharedSession, request: Dict) -> Dict:
    op = request.get("op")
    try:
        if op == "exec":
            command = request["command"]
            timeout = float(request.get("timeout", 30.0))
            return {"output": session.exec_command(command, timeout)}
        if op == "snapshot":
            return {"screen": session.snapshot()}
        if op == "send":
            session.write(request["keys"].encode())
            return {"ok": True}
        if op == "logs":
            lines = int(request.get("lines", 200))
            return {"logs": session.logs(lines)}
        return {"error": f"unknown op: {op}"}
    except Exception as exc:  # noqa: BLE001 -- surface as an error response, never crash the daemon
        return {"error": str(exc)}


def _bind_unix_socket(path: str) -> socket.socket:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    # Frank's human terminal and an agent process are different local
    # users than whatever account runs this daemon; connect() needs write
    # permission on the socket inode, which the default umask would deny.
    os.chmod(path, 0o666)
    sock.listen(8)
    return sock


def _handle_control_conn(session: SharedSession, conn: socket.socket) -> None:
    try:
        conn.settimeout(35.0)
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
        if not data.strip():
            return
        request = json.loads(data.decode(errors="replace"))
        response = handle_control_op(session, request)
        conn.sendall((json.dumps(response) + "\n").encode())
    except Exception as exc:  # noqa: BLE001
        try:
            conn.sendall((json.dumps({"error": str(exc)}) + "\n").encode())
        except OSError:
            pass
    finally:
        conn.close()


def serve_control(session: SharedSession, path: str) -> None:
    sock = _bind_unix_socket(path)
    while True:
        conn, _ = sock.accept()
        threading.Thread(target=_handle_control_conn, args=(session, conn), daemon=True).start()


def _handle_attach_conn(session: SharedSession, conn: socket.socket) -> None:
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            session.write(data)
    except OSError:
        pass
    finally:
        session.detach(conn)
        conn.close()


def serve_attach(session: SharedSession, path: str) -> None:
    sock = _bind_unix_socket(path)
    while True:
        conn, _ = sock.accept()
        session.attach(conn)
        threading.Thread(target=_handle_attach_conn, args=(session, conn), daemon=True).start()


def cmd_serve(args: argparse.Namespace) -> None:
    session = SharedSession(shell=args.shell)
    control_thread = threading.Thread(
        target=serve_control, args=(session, args.control_socket), daemon=True)
    attach_thread = threading.Thread(
        target=serve_attach, args=(session, args.attach_socket), daemon=True)
    control_thread.start()
    attach_thread.start()
    control_thread.join()


def cmd_attach(args: argparse.Namespace) -> None:
    """Human side: join the shared PTY from this terminal. Raw mode, bidirectional
    byte relay, restores the local tty on exit (Ctrl-D / connection close)."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(args.attach_socket)
    stdin_fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(stdin_fd)
    tty.setraw(stdin_fd)
    try:
        while True:
            readable, _, _ = select.select([stdin_fd, sock], [], [])
            if stdin_fd in readable:
                data = os.read(stdin_fd, 4096)
                if not data:
                    break
                sock.sendall(data)
            if sock in readable:
                data = sock.recv(4096)
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
        sock.close()


def cmd_call(args: argparse.Namespace) -> None:
    """Debug/health-check helper: issue one control-socket op and print the
    JSON response. Not used by the daemon itself; useful for `remote-cli call
    snapshot` style manual checks after install."""
    fields: Dict[str, object] = {}
    for item in args.field:
        key, _, value = item.partition("=")
        fields[key] = value
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(args.control_socket)
    sock.sendall((json.dumps({"op": args.op, **fields}) + "\n").encode())
    chunks = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        if chunks[-1].endswith(b"\n"):
            break
    sock.close()
    sys.stdout.write(b"".join(chunks).decode(errors="replace"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remote-cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="run the shared-PTY daemon (systemd ExecStart target)")
    serve_p.add_argument("--shell", default=None, help="shell to spawn (default: $SHELL or /bin/bash)")
    serve_p.add_argument("--control-socket", default=DEFAULT_CONTROL_SOCKET)
    serve_p.add_argument("--attach-socket", default=DEFAULT_ATTACH_SOCKET)
    serve_p.set_defaults(func=cmd_serve)

    attach_p = sub.add_parser("attach", help="join the shared terminal from this tty")
    attach_p.add_argument("--attach-socket", default=DEFAULT_ATTACH_SOCKET)
    attach_p.set_defaults(func=cmd_attach)

    call_p = sub.add_parser("call", help="debug: issue one control-socket op")
    call_p.add_argument("op", choices=["exec", "snapshot", "send", "logs"])
    call_p.add_argument("--field", action="append", default=[], metavar="KEY=VALUE")
    call_p.add_argument("--control-socket", default=DEFAULT_CONTROL_SOCKET)
    call_p.set_defaults(func=cmd_call)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
