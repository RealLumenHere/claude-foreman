#!/usr/bin/env python3
"""openocd_client — stdlib-only driver for OpenOCD's Tcl RPC interface (wq#3881).

No MCP dependency lives here on purpose: server.py is a thin FastMCP wrapper
around this module, and this module is what the test suite can actually
exercise in a sandbox where the `mcp` package isn't installed.

Protocol: OpenOCD's Tcl RPC port (default 6666) accepts the same command set
as its interactive telnet console, one command per request, each response
terminated by a single 0x1a (Ctrl-Z) byte — see the "Tcl RPC server" section
of the OpenOCD User's Guide. `read_memory`/`write_memory` (OpenOCD >=0.11,
confirmed present at 0.12.0 on the maksoor RE workstation per
TOOL_REGISTRY.md) are used instead of the older mdw/mww text commands because
they return a plain Tcl list instead of an addr-prefixed hex dump.
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path
from typing import Sequence

RPC_TERMINATOR = b"\x1a"
VALID_WIDTHS = (8, 16, 32, 64)
VALID_RESET_MODES = ("run", "halt", "init")


class OpenOCDError(RuntimeError):
    """Raised when the RPC link misbehaves or OpenOCD can't be reached."""


class OpenOCDClient:
    """Drives one OpenOCD process + its Tcl RPC socket. Not thread-safe."""

    def __init__(
        self,
        executable: str = "openocd",
        tcl_port: int = 6666,
        telnet_port: int = 4444,
        gdb_port: int = 3333,
        startup_timeout: float = 10.0,
        rpc_timeout: float = 5.0,
    ) -> None:
        self._executable = executable
        self._tcl_port = tcl_port
        self._telnet_port = telnet_port
        self._gdb_port = gdb_port
        self._startup_timeout = startup_timeout
        self._rpc_timeout = rpc_timeout
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self, config_files: Sequence[str], extra_args: Sequence[str] = ()) -> str:
        """Launch OpenOCD with the given -f config files and open the Tcl RPC socket."""
        if self._proc is not None:
            raise OpenOCDError("already connected; call disconnect() first")
        if not config_files:
            raise ValueError("config_files must name at least one -f config")
        args = [self._executable]
        for cfg in config_files:
            args += ["-f", cfg]
        args += [
            "-c", f"tcl_port {self._tcl_port}",
            "-c", f"telnet_port {self._telnet_port}",
            "-c", f"gdb_port {self._gdb_port}",
        ]
        args += list(extra_args)
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as exc:
            raise OpenOCDError(f"failed to launch {self._executable!r}: {exc}") from exc
        self._proc = proc
        try:
            self._sock = self._await_rpc_socket()
        except Exception:
            self._kill_process()
            raise
        return f"connected: pid={proc.pid} tcl_port={self._tcl_port}"

    def _await_rpc_socket(self) -> socket.socket:
        deadline = time.monotonic() + self._startup_timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                out = self._proc.stdout.read() if self._proc.stdout else ""
                raise OpenOCDError(
                    f"openocd exited early (code={self._proc.returncode}): {out[-2000:]}"
                )
            try:
                return socket.create_connection(
                    ("127.0.0.1", self._tcl_port), timeout=self._rpc_timeout
                )
            except OSError as exc:
                last_err = exc
                time.sleep(0.2)
        raise OpenOCDError(f"timed out waiting for Tcl RPC on port {self._tcl_port}: {last_err}")

    def disconnect(self) -> str:
        if self._sock is not None:
            try:
                self._send_raw("exit")
            except OSError:
                pass
            try:
                self._sock.close()
            finally:
                self._sock = None
        pid = self._proc.pid if self._proc is not None else None
        self._kill_process()
        return f"disconnected: pid={pid}"

    def _kill_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _require_connected(self) -> socket.socket:
        if self._sock is None:
            raise OpenOCDError("not connected; call connect() first")
        return self._sock

    def _send_raw(self, cmd: str) -> str:
        sock = self._require_connected()
        sock.sendall(cmd.encode() + RPC_TERMINATOR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                self._sock = None
                raise OpenOCDError("Tcl RPC connection closed unexpectedly")
            chunks.append(chunk)
            if chunks[-1].endswith(RPC_TERMINATOR):
                break
        return b"".join(chunks)[:-1].decode(errors="replace").strip()

    # --- target control -----------------------------------------------

    def target_halt(self) -> str:
        return self._send_raw("halt")

    def target_resume(self, address: int | None = None) -> str:
        cmd = "resume" if address is None else f"resume {address:#x}"
        return self._send_raw(cmd)

    def target_reset(self, mode: str = "run") -> str:
        if mode not in VALID_RESET_MODES:
            raise ValueError(f"invalid reset mode {mode!r}, expected one of {VALID_RESET_MODES}")
        return self._send_raw(f"reset {mode}")

    # --- memory ----------------------------------------------------------

    def read_memory(self, address: int, width: int = 32, count: int = 1, phys: bool = False) -> list[int]:
        if width not in VALID_WIDTHS:
            raise ValueError(f"invalid width {width}, expected one of {VALID_WIDTHS}")
        if count < 1:
            raise ValueError("count must be >= 1")
        cmd = f"read_memory {address:#x} {width} {count}"
        if phys:
            cmd += " phys"
        resp = self._send_raw(cmd)
        return [int(tok, 0) for tok in resp.split()]

    def write_memory(self, address: int, data: Sequence[int], width: int = 32, phys: bool = False) -> str:
        if width not in VALID_WIDTHS:
            raise ValueError(f"invalid width {width}, expected one of {VALID_WIDTHS}")
        if not data:
            raise ValueError("data must be non-empty")
        values = " ".join(str(v) for v in data)
        cmd = f"write_memory {address:#x} {width} {{{values}}}"
        if phys:
            cmd += " phys"
        return self._send_raw(cmd)

    # --- flashing ----------------------------------------------------------

    def flash_write(self, image_path: str, address: int, erase: bool = True, verify: bool = True) -> str:
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"firmware image not found: {image_path}")
        parts = ["flash", "write_image"]
        if erase:
            parts.append("erase")
        parts.append(f'"{path}"')
        parts.append(f"{address:#x}")
        out = self._send_raw(" ".join(parts))
        if verify:
            out = f"{out}\n{self._send_raw(f'verify_image \"{path}\" {address:#x}')}"
        return out

    # --- JTAG chain / boundary scan -----------------------------------

    def scan_chain(self) -> list[dict]:
        return _parse_scan_chain(self._send_raw("scan_chain"))

    def jtag_names(self) -> list[str]:
        return self._send_raw("jtag names").split()

    def boundary_scan(self, tap: str, ir_value: int, dr_length: int) -> str:
        if dr_length <= 0:
            raise ValueError("dr_length must be positive")
        self._send_raw(f"irscan {tap} {ir_value:#x}")
        return self._send_raw(f"drscan {tap} {dr_length} 0")


def _parse_scan_chain(raw: str) -> list[dict]:
    """Parse `scan_chain` table output into one dict per TAP.

    Expected shape (OpenOCD 0.12.0):
        TapName             Enabled  IdCode     Expected   IrLen IrCap IrMask
    -- ------------------- -------- ---------- ---------- ----- ----- ------
     0 stm32f4x.cpu          Y      0x4ba00477 0x4ba00477    4  0x01  0x0f
    """
    taps = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.lower().startswith("tapname"):
            continue
        fields = line.split()
        if len(fields) < 7:
            continue
        idx, name, enabled, idcode, expected, irlen, ircap = fields[:7]
        irmask = fields[7] if len(fields) > 7 else ""
        if not idx.isdigit():
            continue
        taps.append({
            "index": int(idx),
            "name": name,
            "enabled": enabled.upper() == "Y",
            "idcode": idcode,
            "expected": expected,
            "ir_len": int(irlen) if irlen.isdigit() else irlen,
            "ir_cap": ircap,
            "ir_mask": irmask,
        })
    return taps
