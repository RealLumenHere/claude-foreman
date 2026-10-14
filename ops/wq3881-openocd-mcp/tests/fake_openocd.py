#!/usr/bin/env python3
"""Fake `openocd` binary for tests (wq#3881).

Parses the same `-f cfg -c "tcl_port N" ...` argv shape the real OpenOCD
accepts, opens a TCP socket on the requested tcl_port, and speaks the real
Tcl RPC wire format (command bytes, then a single 0x1a terminator) so
openocd_client.OpenOCDClient's connect()/_send_raw() round-trip is exercised
against real sockets and a real subprocess, not a mock.

Responses are canned per recognized command so the test suite can assert on
openocd_client's parsing (e.g. scan_chain table parsing) without needing real
hardware or the real openocd binary.
"""

from __future__ import annotations

import socket
import sys
import threading

TERM = b"\x1a"

SCAN_CHAIN_TABLE = (
    "   TapName             Enabled  IdCode     Expected   IrLen IrCap IrMask\n"
    "-- ------------------- -------- ---------- ---------- ----- ----- ------\n"
    " 0 stm32f4x.cpu          Y      0x4ba00477 0x4ba00477    4  0x01  0x0f\n"
)


def _handle(conn: socket.socket) -> None:
    buf = b""
    with conn:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
            if not buf.endswith(TERM):
                continue
            cmd = buf[:-1].decode()
            buf = b""
            reply = _dispatch(cmd)
            conn.sendall(reply.encode() + TERM)
            if cmd == "exit":
                return


def _dispatch(cmd: str) -> str:
    if cmd == "halt":
        return "target halted due to debug-request, current mode: Thread"
    if cmd.startswith("resume"):
        return ""
    if cmd.startswith("reset"):
        return "JTAG tap: stm32f4x.cpu tap/device found: 0x4ba00477"
    if cmd.startswith("read_memory"):
        count = int(cmd.split()[3])
        return " ".join(str(i + 1) for i in range(count))
    if cmd.startswith("write_memory"):
        return ""
    if cmd.startswith("flash write_image"):
        return "wrote 1024 bytes from file fw.bin in 0.1s"
    if cmd.startswith("verify_image"):
        return "verified 1024 bytes in 0.05s"
    if cmd == "scan_chain":
        return SCAN_CHAIN_TABLE
    if cmd == "jtag names":
        return "stm32f4x.cpu"
    if cmd.startswith("irscan"):
        return ""
    if cmd.startswith("drscan"):
        return "0 1 1 0 1 0 1 0"
    if cmd == "exit":
        return ""
    return f"unknown command: {cmd}"


def main(argv: list[str]) -> int:
    tcl_port = 6666
    i = 0
    while i < len(argv):
        if argv[i] == "-c" and i + 1 < len(argv) and argv[i + 1].startswith("tcl_port "):
            tcl_port = int(argv[i + 1].split()[1])
        i += 1

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", tcl_port))
    srv.listen(1)
    print(f"Listening on port {tcl_port} for tcl connections", flush=True)

    def serve_forever():
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()

    t = threading.Thread(target=serve_forever, daemon=True)
    t.start()
    t.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
