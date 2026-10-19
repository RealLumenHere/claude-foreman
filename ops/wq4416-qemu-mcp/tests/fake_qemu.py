#!/usr/bin/env python3
"""Fake `qemu-system-x86_64` binary for tests (wq#4416).

Parses just enough of the real qemu-system-x86_64 argv shape that
qemu_client.QMPClient.boot() builds (`-qmp unix:PATH,server=on,wait=off`,
`-serial file:PATH`) to stand in for a real QEMU process: it opens a real
AF_UNIX socket at the QMP path and speaks the real QMP wire protocol
(newline-delimited JSON, greeting + capabilities handshake), so
QMPClient.boot()/screenshot()/read_serial()/wait_serial()/type_text()/quit()
round-trip against a real socket and a real subprocess, not a mock. Only the
*responses* are canned, by _dispatch's table — same shape as
ops/wq3881-openocd-mcp/tests/fake_openocd.py for the Tcl RPC wire.

Canned behavior:
- Writes a fixed boot transcript to the -serial file target on startup, so
  wait_serial() has real file content to tail.
- `screendump` writes a real, tiny valid PNG to the requested filename.
- `send-key` is recorded (one JSON line per call) to keys.jsonl next to the
  QMP socket, so tests can assert on the exact chord sequence qemu_type sent.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

GREETING = {
    "QMP": {
        "version": {"qemu": {"major": 8, "minor": 2, "micro": 0}, "package": "fake"},
        "capabilities": [],
    }
}

SERIAL_TRANSCRIPT = (
    "SeaBIOS (version 1.16.0)\n"
    "Booting from DVD/CD...\n"
    "ISOLINUX 6.04 ...\n"
    "Loading vmlinuz... Loading initrd...\n"
    "[    0.000000] Linux version 6.1.0 (limpet)\n"
    "limpet login: "
)

# A valid 1x1 transparent PNG, for screendump to write at the requested path.
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0c0c0040002fe02feccb9e3270000000049454e44ae426082"
)


def _parse_arg(argv: list[str], flag: str) -> str | None:
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _dispatch(req: dict, keys_log_path: Path) -> dict:
    cmd = req.get("execute")
    args = req.get("arguments") or {}
    if cmd == "qmp_capabilities":
        return {"return": {}}
    if cmd == "screendump":
        filename = args.get("filename")
        if filename:
            Path(filename).write_bytes(_PNG_1X1)
        return {"return": {}}
    if cmd == "send-key":
        with open(keys_log_path, "a") as f:
            f.write(json.dumps(args) + "\n")
        return {"return": {}}
    if cmd == "query-status":
        return {"return": {"status": "running", "running": True}}
    if cmd == "quit":
        return {"return": {}}
    return {"error": {"class": "CommandNotFound", "desc": f"unknown command: {cmd}"}}


def _handle(conn: socket.socket, keys_log_path: Path) -> None:
    conn.sendall((json.dumps(GREETING) + "\n").encode())
    buf = b""
    with conn:
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if not line.strip():
                    continue
                req = json.loads(line)
                reply = _dispatch(req, keys_log_path)
                conn.sendall((json.dumps(reply) + "\n").encode())
                if req.get("execute") == "quit":
                    return


def main(argv: list[str]) -> int:
    qmp_arg = _parse_arg(argv, "-qmp")
    serial_arg = _parse_arg(argv, "-serial")
    if not qmp_arg or not qmp_arg.startswith("unix:"):
        print("fake qemu: expected -qmp unix:PATH,...", file=sys.stderr)
        return 1
    sock_path = Path(qmp_arg[len("unix:"):].split(",", 1)[0])
    if serial_arg and serial_arg.startswith("file:"):
        Path(serial_arg[len("file:"):]).write_text(SERIAL_TRANSCRIPT)

    keys_log_path = sock_path.parent / "keys.jsonl"

    if sock_path.exists():
        sock_path.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)
    print(f"Listening on {sock_path}", flush=True)

    def serve_forever() -> None:
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_handle, args=(conn, keys_log_path), daemon=True).start()

    t = threading.Thread(target=serve_forever, daemon=True)
    t.start()
    t.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
