#!/usr/bin/env python3
"""energize_confirm.py — operator-issued confirm tokens for hardware MCP tools
(hal-spec.md §9.1/§9.2a, wq#4747, docs/hardware-re-safety/gap-analysis.md (a')).

The gap this closes: write_target_memory, flash_firmware, write_flash and
erase_chip (and the serial-maksoor energizing tools, wq#5139) each took a
model-supplied bool (`confirm=True`) as their go-ahead. That is a
self-assessment gate — the same agent deciding to energize the target is the
one asserting permission to do so. A confirm token moves the assertion outside
the agent's reach. verify_token() is single-use and capability-scoped, so a
token minted for one action can't be replayed for another or reused after a
leak.

What proves a human (wq#5216, mirroring wq#5175's mailbot fix). The old proof
was `sys.stdin.isatty()`, and `script -qec "python3 energize_confirm.py issue
serial.write" /dev/null` hands any lumen shell a pty, so an agent could mint its
own token; it could also just write a token file into the (env-overridable)
token dir. The proof is now a secret Frank holds: an approval passphrase he sets
once (`set-passphrase`) and types at each mint.

  * The passphrase is stretched with scrypt into an Ed25519 signing seed. Only
    the PUBLIC key (plus salt and scrypt params) is stored, in VERIFIER, a fixed
    path no environment variable can redirect.
  * issue signs (sha256(token), capability, expires_at) with that seed. A wrong
    passphrase derives a different key, so nothing is minted.
  * verify_token checks the signature against the stored public key. A
    hand-written token file, an old unsigned record, or a record whose
    capability/expiry was edited is rejected, and reading every file on the box
    (verifier.json included) does not yield a key that can sign one.

A pty from script(1) still reaches the passphrase prompt; without the passphrase
it mints nothing (harness/tests/test_energize_confirm.py drives script(1) both
ways).

What it is not. lumen has NOPASSWD sudo and can edit this file, so an agent that
deliberately tampers (replaces VERIFIER with its own public key, patches
verify_token) is outside this gate. guard.sh and action_gate.py refuse the mint
CLI and both directories by name for agent sessions (wq#5216); the residual is
the same trust as every other on-box control.

Ed25519 is the RFC 8032 §6 reference implementation, inlined so the verifier
runs under any interpreter the hardware MCP servers use (venv, `python -I`)
with no third-party dependency. Signing happens once per mint at Frank's
terminal, so the reference code's speed and lack of constant-time arithmetic
are not a concern here. The RFC test vectors are pinned in the tests.

Importable (action_gate.py's PreToolUse hook calls verify_token directly) and
a standalone CLI for hosts where the hardware MCP server runs without the
rest of this tree on the path (same shape as flashrom_ops.py's HAL_SCRIPT
subprocess call out to another ops/ bundle):

    python3 energize_confirm.py issue  <capability> [--ttl SECONDS]   (asks for the passphrase)
    python3 energize_confirm.py verify <capability> <token>
    python3 energize_confirm.py set-passphrase                         (set, or rotate given the current one)
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import time
from datetime import datetime, timezone

# Module constants, never env (wq#5216): whoever chooses the verifier path chooses the
# passphrase, and whoever chooses the token dir chooses which records are read.
# Tests patch the attributes.
TOKEN_DIR = "/home/lumen/re/.energize-confirm"
VERIFIER_DIR = "/home/lumen/.config/energize-confirm"
VERIFIER = f"{VERIFIER_DIR}/verifier.json"
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 3600
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1  # 32 MiB, ~0.1 s per guess
MIN_PASSPHRASE = 12
FAIL_DELAY = 2.0
RECORD_VERSION = 2
_MSG_DOMAIN = b"energize-confirm/v2"


class EnergizeConfirmError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Ed25519, RFC 8032 §6 reference implementation (verbatim structure).
# ---------------------------------------------------------------------------
_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _modp_inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _modp_inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512_modq(s: bytes) -> int:
    return int.from_bytes(hashlib.sha512(s).digest(), "little") % _Q


def _point_add(P, Q):
    A, B = (P[1] - P[0]) * (Q[1] - Q[0]) % _P, (P[1] + P[0]) * (Q[1] + Q[0]) % _P
    C, D = 2 * P[3] * Q[3] * _D % _P, 2 * P[2] * Q[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def _point_mul(s: int, P):
    Q = (0, 1, 1, 0)  # neutral element
    while s > 0:
        if s & 1:
            Q = _point_add(Q, P)
        P = _point_add(P, P)
        s >>= 1
    return Q


def _point_equal(P, Q) -> bool:
    if (P[0] * Q[2] - Q[0] * P[2]) % _P != 0:
        return False
    return (P[1] * Q[2] - Q[1] * P[2]) % _P == 0


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _modp_inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_G_Y = 4 * _modp_inv(5) % _P
_G_X = _recover_x(_G_Y, 0)
_G = (_G_X, _G_Y, 1, _G_X * _G_Y % _P)


def _point_compress(P) -> bytes:
    zinv = _modp_inv(P[2])
    x, y = P[0] * zinv % _P, P[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _secret_expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("bad Ed25519 seed length")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public(secret: bytes) -> bytes:
    a, _ = _secret_expand(secret)
    return _point_compress(_point_mul(a, _G))


def ed25519_sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _secret_expand(secret)
    A = _point_compress(_point_mul(a, _G))
    r = _sha512_modq(prefix + msg)
    Rs = _point_compress(_point_mul(r, _G))
    h = _sha512_modq(Rs + A + msg)
    s = (r + h * a) % _Q
    return Rs + int.to_bytes(s, 32, "little")


def ed25519_verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _point_decompress(public)
    if not A:
        return False
    Rs = signature[:32]
    R = _point_decompress(Rs)
    if not R:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _sha512_modq(Rs + public + msg)
    return _point_equal(_point_mul(s, _G), _point_add(R, _point_mul(h, A)))


# ---------------------------------------------------------------------------
# Passphrase verifier
# ---------------------------------------------------------------------------
def _seed(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=64 << 20, dklen=32)


def _load_verifier() -> dict | None:
    """The stored verifier, or None if Frank has not set a passphrase. Raises
    EnergizeConfirmError if it exists but is malformed or writable by anyone but
    its owner (a group/world-writable verifier is not one only Frank set)."""
    try:
        st = os.stat(VERIFIER)
        with open(VERIFIER) as f:
            v = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise EnergizeConfirmError(f"energize-confirm verifier {VERIFIER} is unreadable ({e})")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise EnergizeConfirmError(f"energize-confirm verifier {VERIFIER} is group/world-writable")
    try:
        bytes.fromhex(v["salt"]), int(v["n"]), int(v["r"]), int(v["p"])
        if len(bytes.fromhex(v["pub"])) != 32:
            raise ValueError("pub is not 32 bytes")
    except (ValueError, KeyError, TypeError) as e:
        raise EnergizeConfirmError(f"energize-confirm verifier {VERIFIER} is malformed ({e})")
    return v


def _seed_for(v: dict, passphrase: str) -> bytes | None:
    """The signing seed if `passphrase` matches verifier `v`, else None."""
    seed = _seed(passphrase, bytes.fromhex(v["salt"]), int(v["n"]), int(v["r"]), int(v["p"]))
    if hmac.compare_digest(ed25519_public(seed), bytes.fromhex(v["pub"])):
        return seed
    return None


def _write_verifier(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE:
        raise EnergizeConfirmError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
    salt = secrets.token_bytes(16)
    seed = _seed(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    rec = {"v": RECORD_VERSION, "kdf": "scrypt", "sig": "ed25519",
           "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt.hex(),
           "pub": ed25519_public(seed).hex(), "set_at": datetime.now(timezone.utc).isoformat()}
    os.makedirs(os.path.dirname(VERIFIER), mode=0o700, exist_ok=True)
    tmp = f"{VERIFIER}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, VERIFIER)


def set_passphrase(*, _getpass=getpass.getpass) -> str:
    """First set, or rotation given the current passphrase — never a silent
    overwrite. Rotation changes the public key, so every outstanding token dies."""
    v = _load_verifier()
    if v is not None and _seed_for(v, _getpass("current energize-confirm passphrase: ")) is None:
        time.sleep(FAIL_DELAY)
        raise EnergizeConfirmError("wrong passphrase, verifier unchanged")
    new = _getpass(f"new energize-confirm passphrase ({MIN_PASSPHRASE}+ chars): ")
    if new != _getpass("again: "):
        raise EnergizeConfirmError("passphrases differ, verifier unchanged")
    _write_verifier(new)
    return "rotated" if v else "set"


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_path(token: str) -> str:
    return os.path.join(TOKEN_DIR, f"{_token_hash(token)}.json")


def _message(token_hash: str, capability: str, expires_at: int) -> bytes:
    return b"\n".join([_MSG_DOMAIN, token_hash.encode(), capability.encode("utf-8"),
                       str(int(expires_at)).encode()])


def issue_token(capability: str, ttl_seconds: int = DEFAULT_TTL_SECONDS, *,
                _getpass=getpass.getpass) -> str:
    """Mint a confirm token for `capability`. Requires Frank's energize-confirm
    passphrase: the token record is signed with the key it derives, and
    verify_token rejects any record that key did not sign."""
    if not capability:
        raise EnergizeConfirmError("capability is required")
    ttl_seconds = int(ttl_seconds)
    if ttl_seconds > MAX_TTL_SECONDS:
        raise EnergizeConfirmError(f"--ttl is capped at {MAX_TTL_SECONDS}s")
    v = _load_verifier()
    if v is None:
        raise EnergizeConfirmError(
            f"no energize-confirm passphrase is set ({VERIFIER} missing), nothing minted; "
            f"Frank sets one: python3 {os.path.abspath(__file__)} set-passphrase")
    seed = _seed_for(v, _getpass(f"energize-confirm passphrase to authorize {capability}: "))
    if seed is None:
        time.sleep(FAIL_DELAY)
        raise EnergizeConfirmError("wrong passphrase, no token minted")
    token = secrets.token_hex(32)
    th = _token_hash(token)
    expires_at = int(time.time()) + ttl_seconds
    record = {"v": RECORD_VERSION, "capability": capability, "expires_at": expires_at,
              "sig": ed25519_sign(seed, _message(th, capability, expires_at)).hex()}
    os.makedirs(TOKEN_DIR, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(TOKEN_DIR, f"{th}.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    return token


def verify_token(token: str, capability: str) -> bool:
    """True iff `token` was minted for exactly `capability`, has not expired, and
    its record carries a signature by the passphrase-derived key in VERIFIER.
    Single-use: the token file is removed on the first check regardless of
    outcome, so a leaked or replayed token cannot authorize a second action.
    Fails closed on any error (no verifier, malformed record, bad signature)."""
    if not token or not capability or not isinstance(token, str):
        return False
    path = _token_path(token)
    try:
        with open(path) as f:
            record = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    try:
        v = _load_verifier()
        if v is None or not isinstance(record, dict):
            return False
        if record.get("v") != RECORD_VERSION or record.get("capability") != capability:
            return False
        expires_at = record.get("expires_at")
        if not isinstance(expires_at, int) or isinstance(expires_at, bool) or time.time() >= expires_at:
            return False
        sig = bytes.fromhex(record.get("sig", ""))
        return ed25519_verify(bytes.fromhex(v["pub"]),
                              _message(_token_hash(token), capability, expires_at), sig)
    except (EnergizeConfirmError, ValueError, TypeError, AttributeError):
        return False


def _need_tty(what: str) -> None:
    # Usability only, not the security control (a pty from script(1) passes it).
    if not sys.stdin.isatty():
        raise EnergizeConfirmError(f"{what} reads a passphrase from a terminal; run it in one")


def _main(argv: list[str]) -> int:
    try:
        if len(argv) >= 2 and argv[0] == "issue":
            capability = argv[1]
            ttl = DEFAULT_TTL_SECONDS
            if "--ttl" in argv:
                ttl = int(argv[argv.index("--ttl") + 1])
            _need_tty("issue")
            print(issue_token(capability, ttl))
            return 0
        if len(argv) >= 3 and argv[0] == "verify":
            capability, token = argv[1], argv[2]
            return 0 if verify_token(token, capability) else 1
        if len(argv) == 1 and argv[0] == "set-passphrase":
            _need_tty("set-passphrase")
            print(f"energize-confirm passphrase {set_passphrase()}: {VERIFIER}")
            return 0
    except (EnergizeConfirmError, ValueError, EOFError, KeyboardInterrupt) as e:
        print(str(e) or type(e).__name__, file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
