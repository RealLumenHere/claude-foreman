#!/usr/bin/env python3
"""qemu_client — stdlib-only driver for QEMU's QMP control socket (wq#4416).

No MCP dependency lives here on purpose: server.py is a thin FastMCP wrapper
around this module, and this module is what the test suite can actually
exercise in a sandbox where the `mcp` package isn't installed (same split as
ops/wq3881-openocd-mcp/openocd_client.py).

Protocol: QEMU's QMP control socket speaks newline-delimited JSON. On connect
it sends a greeting line (`{"QMP": {...}}`); the client must then issue
`qmp_capabilities` before any other command is accepted. Each subsequent
command is one `{"execute": ..., "arguments": ...}` line in, one or more
`{"event": ...}` lines (ignored here) followed by exactly one `{"return": ...}`
or `{"error": ...}` line out. See QEMU's qemu-qmp-ref.

Launch flags (boot()) and the serial-as-tie-breaker rationale follow
projects/limpet/docs/iso-test-pipeline.md section 3: `-vnc :N,share=allowed`
lets a human VNC viewer and this client's screenshot/type calls attach to the
one display concurrently; `-serial file:...` is the tailable log qemu_serial/
qemu_wait_serial read from, kept as a plain file (not yet the chardev/socket
upgrade iso-test-pipeline.md section 6 describes for the follow-on interactive
pipeline) so it stays byte-compatible with the existing
`stage12_qemu_test` (`limpet/iso-builder/build.sh:557`) serial contract;
`-qmp unix:...,server=on,wait=off` is a unix socket (no network bind, same
fail-closed posture as limpet-hald's control socket) that does not block
guest boot on a client attaching; `-display none` suppresses only the local
SDL/GTK window (Lumen is headless) — the display itself is still served over
VNC; `-no-reboot` turns a guest reboot loop into an observable terminal state.

Prior art (searched before writing this, doctrine): nagy/qemu-mcp-server
wraps the same QMP-socket-plus-serial-socket shape with a generic
`execute_qmp` passthrough and `read_serial`/`write_serial`, but has no
dedicated boot/screenshot/type tools and expects QEMU already launched
out-of-band. SUSE's openQA (os-autoinst) and HashiCorp Packer's QEMU builder
both drive an install over the same `screendump` (screenshot) +
`send-key`/`sendkey` (keystroke) QMP primitives this module uses for
qemu_screenshot/qemu_type. None of the three is a drop-in match for the
ticket's named tool surface (qemu_boot/qemu_screenshot/qemu_serial/
qemu_wait_serial/qemu_type), so this is a new, small client built directly on
the QMP wire protocol rather than a wrapped dependency.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Sequence

DEFAULT_QEMU_BINARY = "qemu-system-x86_64"
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_COMMAND_TIMEOUT = 10.0

_SHIFT = "shift"

# ASCII -> QKeyCode map for qemu_type's `send-key` chords. Covers the
# characters a bootloader/installer prompt needs: letters, digits, the US
# keyboard's unshifted/shifted punctuation rows, enter/space/tab/backspace.
# QKeyCode names per QEMU's qapi/ui.json.
_KEYCODE_MAP: dict[str, str | tuple[str, str]] = {
    **{c: c for c in "abcdefghijklmnopqrstuvwxyz"},
    **{c.upper(): (_SHIFT, c) for c in "abcdefghijklmnopqrstuvwxyz"},
    **{d: d for d in "0123456789"},
    " ": "spc",
    "\n": "ret",
    "\t": "tab",
    "\b": "backspace",
    "-": "minus",
    "=": "equal",
    "_": (_SHIFT, "minus"),
    "+": (_SHIFT, "equal"),
    ".": "dot",
    ",": "comma",
    "/": "slash",
    ";": "semicolon",
    "'": "apostrophe",
    "[": "bracket_left",
    "]": "bracket_right",
    "\\": "backslash",
    ":": (_SHIFT, "semicolon"),
    '"': (_SHIFT, "apostrophe"),
    "<": (_SHIFT, "comma"),
    ">": (_SHIFT, "dot"),
    "?": (_SHIFT, "slash"),
    "!": (_SHIFT, "1"),
    "@": (_SHIFT, "2"),
    "#": (_SHIFT, "3"),
    "$": (_SHIFT, "4"),
    "%": (_SHIFT, "5"),
    "^": (_SHIFT, "6"),
    "&": (_SHIFT, "7"),
    "*": (_SHIFT, "8"),
    "(": (_SHIFT, "9"),
    ")": (_SHIFT, "0"),
}


class QMPError(RuntimeError):
    """Raised when the QMP link misbehaves or a command returns an error."""


class QMPClient:
    """Drives one QEMU process + its QMP unix-socket control channel. Not thread-safe."""

    def __init__(
        self,
        executable: str = DEFAULT_QEMU_BINARY,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ) -> None:
        self._executable = executable
        self._connect_timeout = connect_timeout
        self._command_timeout = command_timeout
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._buf = b""
        self._qmp_sock_path: str | None = None
        self._serial_log_path: str | None = None
        self._serial_offset = 0

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # --- lifecycle -------------------------------------------------------

    def boot(
        self,
        iso_path: str,
        run_dir: str,
        memory_mb: int = 4096,
        smp: int = 2,
        machine: str = "q35",
        vnc_display: int = 1,
        extra_args: Sequence[str] = (),
    ) -> dict:
        """Launch qemu-system-x86_64 booting `iso_path`, with a shared VNC
        display plus the QMP + serial sinks the rest of this client reads
        from. See module docstring for why each flag is load-bearing."""
        if self._proc is not None:
            raise QMPError("already booted; call quit() first")
        if memory_mb < 1:
            raise ValueError("memory_mb must be >= 1")
        if smp < 1:
            raise ValueError("smp must be >= 1")
        iso = Path(iso_path)
        if not iso.is_file():
            raise FileNotFoundError(f"ISO not found: {iso_path}")
        run = Path(run_dir)
        run.mkdir(parents=True, exist_ok=True)
        qmp_sock = run / "qmp.sock"
        serial_log = run / "serial.log"
        if qmp_sock.exists():
            qmp_sock.unlink()
        args = [
            self._executable,
            "-machine", machine,
            "-m", str(memory_mb),
            "-smp", str(smp),
            "-cdrom", str(iso),
            "-boot", "d",
            "-no-reboot",
            "-vnc", f":{vnc_display},share=allowed",
            "-serial", f"file:{serial_log}",
            "-qmp", f"unix:{qmp_sock},server=on,wait=off",
            "-display", "none",
            *extra_args,
        ]
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except OSError as exc:
            raise QMPError(f"failed to launch {self._executable!r}: {exc}") from exc
        self._proc = proc
        try:
            self._connect_qmp(qmp_sock)
        except Exception:
            self._kill_process()
            raise
        self._qmp_sock_path = str(qmp_sock)
        self._serial_log_path = str(serial_log)
        self._serial_offset = 0
        return {
            "pid": proc.pid,
            "qmp_sock": self._qmp_sock_path,
            "serial_log": self._serial_log_path,
            "vnc_port": 5900 + vnc_display,
        }

    def _connect_qmp(self, sock_path: Path) -> None:
        deadline = time.monotonic() + self._connect_timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                out = self._proc.stdout.read() if self._proc.stdout else ""
                raise QMPError(f"qemu exited early (code={self._proc.returncode}): {out[-2000:]}")
            if sock_path.exists():
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self._command_timeout)
                try:
                    sock.connect(str(sock_path))
                    self._buf = b""
                    self._sock = sock
                    greeting = self._readline()
                    if not greeting or "QMP" not in json.loads(greeting):
                        raise QMPError(f"unexpected QMP greeting: {greeting!r}")
                    self._send_command("qmp_capabilities")
                    return
                except Exception:
                    self._sock = None
                    sock.close()
                    raise
            time.sleep(0.1)
        raise QMPError(f"timed out waiting for QMP socket {sock_path}: {last_err}")

    def quit(self) -> str:
        if self._sock is not None:
            try:
                self._send_command("quit")
            except QMPError:
                pass
            try:
                self._sock.close()
            finally:
                self._sock = None
        pid = self._proc.pid if self._proc is not None else None
        self._kill_process()
        return f"stopped: pid={pid}"

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

    # --- QMP wire protocol -------------------------------------------------

    def _require_connected(self) -> socket.socket:
        if self._sock is None:
            raise QMPError("not booted; call boot() first")
        return self._sock

    def _readline(self) -> str:
        while b"\n" not in self._buf:
            try:
                chunk = self._sock.recv(65536)
            except socket.timeout as exc:
                raise QMPError(f"QMP read timed out: {exc}") from exc
            if not chunk:
                return ""
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode()

    def _send_command(self, execute: str, arguments: dict | None = None) -> dict:
        sock = self._require_connected()
        payload: dict = {"execute": execute}
        if arguments is not None:
            payload["arguments"] = arguments
        sock.sendall((json.dumps(payload) + "\n").encode())
        while True:
            line = self._readline()
            if not line:
                self._sock = None
                raise QMPError("QMP connection closed unexpectedly")
            msg = json.loads(line)
            if "event" in msg:
                continue
            if "error" in msg:
                raise QMPError(f"QMP error executing {execute}: {msg['error']}")
            return msg.get("return", {})

    # --- sensors: screenshot + serial ------------------------------------

    def screenshot(self, out_path: str, image_format: str = "png") -> str:
        """Capture the guest framebuffer to `out_path` via QMP screendump."""
        args: dict = {"filename": str(out_path)}
        if image_format:
            args["format"] = image_format
        self._send_command("screendump", args)
        return str(out_path)

    def read_serial(self, max_chars: int | None = None) -> str:
        """Return serial output appended since the last read_serial()/
        wait_serial() call — a tail, never a re-read of bytes already
        returned (iso-test-pipeline.md section 4)."""
        if self._serial_log_path is None:
            raise QMPError("not booted; call boot() first")
        with open(self._serial_log_path, "rb") as f:
            f.seek(self._serial_offset)
            data = f.read()
        self._serial_offset += len(data)
        text = data.decode(errors="replace")
        if max_chars is not None and len(text) > max_chars:
            text = text[-max_chars:]
        return text

    def wait_serial(
        self,
        pattern: str,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.5,
        regex: bool = False,
    ) -> dict:
        """Poll the serial console until `pattern` appears or `timeout_s`
        elapses. This is the sensor that sees boot errors while the
        framebuffer is still black (section 1/6 of the design doc)."""
        if timeout_s < 0:
            raise ValueError("timeout_s must be >= 0")
        matcher = re.compile(pattern) if regex else None
        start = time.monotonic()
        deadline = start + timeout_s
        collected: list[str] = []
        while True:
            chunk = self.read_serial()
            if chunk:
                collected.append(chunk)
            haystack = "".join(collected)
            found = bool(matcher.search(haystack)) if matcher else (pattern in haystack)
            if found or time.monotonic() >= deadline:
                return {
                    "matched": found,
                    "text": haystack,
                    "elapsed_s": round(time.monotonic() - start, 2),
                }
            time.sleep(poll_interval_s)

    # --- actuator: keystrokes ----------------------------------------------

    def type_text(self, text: str, key_delay_s: float = 0.05) -> str:
        """Type `text` into the guest via QMP send-key, one chord per
        character."""
        for ch in text:
            self._send_key(ch)
            if key_delay_s:
                time.sleep(key_delay_s)
        return f"typed {len(text)} chars"

    def _send_key(self, ch: str) -> None:
        mapped = _KEYCODE_MAP.get(ch)
        if mapped is None:
            raise ValueError(f"no QKeyCode mapping for character {ch!r}")
        codes = [mapped] if isinstance(mapped, str) else list(mapped)
        keys = [{"type": "qcode", "data": code} for code in codes]
        self._send_command("send-key", {"keys": keys})
