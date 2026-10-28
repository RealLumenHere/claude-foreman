#!/usr/bin/env python3
"""energize_confirm.py — operator-issued confirm tokens for hardware MCP tools
(hal-spec.md §9.1/§9.2a, wq#4747, docs/hardware-re-safety/gap-analysis.md (a')).

The gap this closes: write_target_memory, flash_firmware, write_flash and
erase_chip each took a model-supplied bool (`confirm=True`) as their go-ahead.
That is a self-assessment gate — the same agent deciding to energize the
target is the one asserting permission to do so. A confirm token fixes that
by moving the assertion outside the agent's reach: issue_token() refuses to
run unless stdin is a real TTY, which a headless/agent session never has, so
only a human physically at the keyboard can mint one. verify_token() is
single-use and capability-scoped, so a token minted for one action can't be
replayed for another or reused after a leak.

Importable (action_gate.py's PreToolUse hook calls verify_token directly) and
a standalone CLI for hosts where the hardware MCP server runs without the
rest of this tree on the path (same shape as flashrom_ops.py's HAL_SCRIPT
subprocess call out to another ops/ bundle):

    python3 energize_confirm.py issue  <capability> [--ttl SECONDS]
    python3 energize_confirm.py verify <capability> <token>
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time

TOKEN_DIR = os.environ.get("ENERGIZE_CONFIRM_DIR", os.path.expanduser("~/re/.energize-confirm"))
DEFAULT_TTL_SECONDS = 300


class EnergizeConfirmError(RuntimeError):
    pass


def _token_path(token: str) -> str:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return os.path.join(TOKEN_DIR, f"{token_hash}.json")


def issue_token(capability: str, ttl_seconds: int = DEFAULT_TTL_SECONDS, *, _isatty=None) -> str:
    """Mint a confirm token for `capability`. Fails closed outside a real
    terminal — the one thing a headless agent session cannot forge."""
    isatty = sys.stdin.isatty() if _isatty is None else _isatty
    if not isatty:
        raise EnergizeConfirmError(
            "energize-confirm tokens may only be issued from an interactive terminal; "
            "a headless/agent session cannot mint one for itself"
        )
    token = secrets.token_hex(32)
    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
    path = _token_path(token)
    record = {"capability": capability, "expires_at": time.time() + ttl_seconds}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    return token


def verify_token(token: str, capability: str) -> bool:
    """True iff `token` was minted for exactly `capability` and has not expired.
    Single-use: the token file is removed on the first check regardless of
    outcome, so a leaked or replayed token cannot authorize a second action."""
    if not token or not capability:
        return False
    path = _token_path(token)
    try:
        with open(path) as f:
            record = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    if record.get("capability") != capability:
        return False
    return time.time() < record.get("expires_at", 0)


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "issue":
        capability = argv[1]
        ttl = DEFAULT_TTL_SECONDS
        if "--ttl" in argv:
            ttl = int(argv[argv.index("--ttl") + 1])
        try:
            token = issue_token(capability, ttl)
        except EnergizeConfirmError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(token)
        return 0
    if len(argv) >= 3 and argv[0] == "verify":
        capability, token = argv[1], argv[2]
        return 0 if verify_token(token, capability) else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
