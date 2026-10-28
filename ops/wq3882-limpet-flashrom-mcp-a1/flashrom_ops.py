"""wq#3882 — flashrom operations, independent of the MCP SDK.

Split out of server.py so this logic is testable in a build lane where `mcp` isn't
pip-installed (same reason ops/wq1151-lumen-kb splits kb_paths.py/kb_distill.py out of
its server.py — see that ops dir's requirements.txt). server.py imports this module and
adds nothing but the @mcp.tool() decoration.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FLASHROM_BIN = os.environ.get("LIMPET_FLASHROM_BIN") or shutil.which("flashrom") or "/usr/sbin/flashrom"
DEFAULT_TIMEOUT_SEC = 120
ERASE_TIMEOUT_SEC = 300

# wq#3809's HAL already enumerates connected flash programmers (CH341A/TL866) by USB
# VID:PID; identify_programmer reuses it instead of re-deriving lsusb signatures here.
HAL_SCRIPT = Path(os.environ.get(
    "LIMPET_HAL_SCRIPT", "/home/lumen/foreman/ops/wq3809-limpet-hal/hal.py"
))

# wq#4747 (hal-spec.md §9.2a): write_flash/erase_chip energize the target, so they
# refuse without an operator-issued confirm token, verified by shelling out to
# energize_confirm.py -- same cross-ops-bundle subprocess pattern as HAL_SCRIPT above,
# so this server stays runnable on a host without the rest of the harness tree.
ENERGIZE_CONFIRM_SCRIPT = Path(os.environ.get(
    "ENERGIZE_CONFIRM_SCRIPT", "/home/lumen/foreman/harness/energize_confirm.py"
))

CHIP_LINE_RE = re.compile(
    r'Found\s+(?P<vendor>\S+)\s+flash chip\s+"(?P<chip>[^"]+)"\s+'
    r'\((?P<size_kb>\d+)\s*kB,\s*(?P<bus>[^)]+)\)',
    re.IGNORECASE,
)


class FlashromError(RuntimeError):
    pass


def _verify_confirm_token(capability: str, confirm_token: str) -> bool:
    """Shell out to energize_confirm.py's verifier. Split out from
    _require_confirm_token so tests can mock this one call without also faking
    every other subprocess.run in this module (the flashrom invocations)."""
    try:
        rc = subprocess.run(
            [sys.executable, str(ENERGIZE_CONFIRM_SCRIPT), "verify", capability, confirm_token],
            capture_output=True, timeout=10,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return False  # verifier unreachable -- fail closed, never fail open
    return rc == 0


def _require_confirm_token(capability: str, confirm_token: str) -> None:
    """Refuse `capability` unless `confirm_token` was minted by an operator
    (energize_confirm.issue_token, which refuses outside a real TTY). A bool the
    calling model passes does not count (hal-spec.md §9.2a)."""
    if not confirm_token:
        raise FlashromError(
            f"{capability} refused: missing confirm_token (operator-issued -- run "
            f"'python3 {ENERGIZE_CONFIRM_SCRIPT} issue {capability}' from an "
            "interactive terminal, not from this agent session)"
        )
    if not _verify_confirm_token(capability, confirm_token):
        raise FlashromError(f"{capability} refused: invalid or expired confirm_token")


def _run(args: list[str], timeout: int) -> dict:
    """Run flashrom with an argv list (never shell=True) and return a structured result.

    Never raises on a nonzero flashrom exit — that's flashrom reporting a real
    chip/programmer-level failure (e.g. verify mismatch), which the caller needs to see,
    not an exception that hides the stdout/stderr an operator needs to act on. Only a
    missing binary or a timeout (both indicate the command never ran to completion) raise.
    """
    command = [FLASHROM_BIN, *args]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout, shell=False)
    except FileNotFoundError as exc:
        raise FlashromError(f"flashrom binary not found at {FLASHROM_BIN!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FlashromError(f"flashrom timed out after {timeout}s: {' '.join(args)}") from exc
    chips = [
        {
            "vendor": m["vendor"],
            "chip": m["chip"],
            "size_kb": int(m["size_kb"]),
            "bus": m["bus"].strip(),
        }
        for m in CHIP_LINE_RE.finditer(proc.stdout)
    ]
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "chips": chips,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _programmer_args(programmer: str, chip: str) -> list[str]:
    args = ["-p", programmer]
    if chip:
        args += ["-c", chip]
    return args


def detect_chip(programmer: str, chip: str = "") -> dict:
    """Probe the flash chip wired to `programmer` without reading, writing, or erasing
    anything — flashrom's default action when no -r/-w/-E/-v flag is given."""
    return _run(_programmer_args(programmer, chip), DEFAULT_TIMEOUT_SEC)


def read_flash(
    programmer: str, output_path: str, chip: str = "", region: str = "", layout_file: str = ""
) -> dict:
    """Read the full flash chip (or one named `region`, via flashrom's -i) and save it to
    `output_path`.

    flashrom's -i rejects the command line before even probing the chip unless a layout
    source is also given (-l <layout_file>, --ifd, --fmap, ...); this wrapper only
    supports -l, so `region` requires `layout_file` (wq#3894 — see
    reports/3890_judgment_wq3882_flashrom_region_flag.md for the real-binary repro)."""
    if region and not layout_file:
        raise FlashromError(
            f"read_flash region={region!r} requires layout_file=<path> (flashrom's -l): "
            "-i is rejected before the chip is even probed without a layout source"
        )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = _programmer_args(programmer, chip) + ["-r", str(out)]
    if layout_file:
        args += ["-l", layout_file]
    if region:
        args += ["-i", region]
    result = _run(args, DEFAULT_TIMEOUT_SEC)
    result["output_path"] = str(out)
    return result


def write_flash(programmer: str, input_path: str, confirm_token: str = "",
                 chip: str = "", verify: bool = True) -> dict:
    """Write `input_path` to the flash chip attached via `programmer`. Verifies
    automatically unless `verify=False` (flashrom's -n/--noverify). Energizing
    (hal-spec.md §9.2a): refuses without an operator-issued `confirm_token`."""
    _require_confirm_token("flashrom.write_flash", confirm_token)
    src = Path(input_path)
    if not src.is_file():
        raise FileNotFoundError(f"no such file: {input_path}")
    args = _programmer_args(programmer, chip) + ["-w", str(src)]
    if not verify:
        args.append("-n")
    return _run(args, DEFAULT_TIMEOUT_SEC)


def verify_flash(programmer: str, input_path: str, chip: str = "") -> dict:
    """Verify the flash chip attached via `programmer` matches `input_path`, without
    writing anything (flashrom's -v)."""
    src = Path(input_path)
    if not src.is_file():
        raise FileNotFoundError(f"no such file: {input_path}")
    args = _programmer_args(programmer, chip) + ["-v", str(src)]
    return _run(args, DEFAULT_TIMEOUT_SEC)


def erase_chip(programmer: str, confirm_token: str = "", chip: str = "") -> dict:
    """Erase the flash chip attached via `programmer` (flashrom's -E). Energizing
    and irreversible without a prior backup (hal-spec.md §9.2a): refuses without
    an operator-issued `confirm_token` -- a model-supplied bool does not count."""
    _require_confirm_token("flashrom.erase_chip", confirm_token)
    args = _programmer_args(programmer, chip) + ["-E"]
    return _run(args, ERASE_TIMEOUT_SEC)


def identify_programmer() -> list[dict]:
    """Report which flash programmers (CH341A, TL866/XGecu, etc.) are physically
    connected right now, via the LIMPET HAL's USB VID:PID detection (wq#3809)."""
    if not HAL_SCRIPT.is_file():
        raise FlashromError(f"LIMPET HAL script not found at {HAL_SCRIPT} (set LIMPET_HAL_SCRIPT)")
    try:
        proc = subprocess.run(
            [sys.executable, str(HAL_SCRIPT), "detect"],
            capture_output=True, text=True, timeout=15, shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FlashromError("hal.py detect timed out after 15s") from exc
    if proc.returncode != 0:
        raise FlashromError(f"hal.py detect failed (exit {proc.returncode}): {proc.stderr.strip()}")
    try:
        devices = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FlashromError(f"hal.py detect produced non-JSON output: {proc.stdout[:200]!r}") from exc
    return [d for d in devices if d.get("hw_type") == "flash_programmer"]
