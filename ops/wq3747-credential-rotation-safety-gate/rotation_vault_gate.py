#!/usr/bin/env python3
"""rotation_vault_gate — shared credential-rotation safety gate (wq#3747).

SAFETY GAP this closes: every rotate-* tool on the fleet (rotate-fleet-pw.sh,
rotate-provider-keys, rotate-github-cred, rotate-gitea-cred, gitea-rotate-finish,
github-pat-rotate — all in /home/lumen/bin) destroys the OLD value of a
credential the moment it writes the NEW one. Each already keeps a local
*.bak.<ts> file of the old value for its own rollback, but nothing checks that
the old value also survives somewhere durable (Vault) before that destruction
happens — a bad rotation plus a lost/shredded local backup loses the
credential for good.

This module is the gate a rotation script calls right before it overwrites a
credential:

    if not require_backup("fleet-pw", old_value):
        log_rotation("rotate-fleet-pw.sh", "fleet-pw", old_value, None, "refused_no_vault_backup")
        die("no Vault backup found — refusing to rotate")
    ...do the rotation...
    log_rotation("rotate-fleet-pw.sh", "fleet-pw", old_value, new_value, "rotated")

"Vault" here is the fleet's Vaultwarden instance, reached through the `bw` CLI
(the same tool vault-put-note uses). This gate never prompts for or reads a
master password: it only looks at whatever Vault session the *caller's* shell
already unlocked (BW_SESSION inherited from the environment). If the vault
isn't already unlocked, or the backup item isn't there, or it doesn't match,
require_backup fails closed — refuses — rather than finding a way around the
check. Create the backup first, in the same attended shell, with
vault_rotation_backup.py, then run the rotation script from that shell.

Every fingerprint below is sha256[:12] of the value — the same convention
rotate-provider-keys.sha12() and rotate-github-cred.fp() already use. Plaintext
credential values are never printed or logged by this module.

wq#3747 scope note: this module, and the harness/guard.sh enforcement wired to
it, ship from the foreman repo (this ticket's assigned worktree). Importing it
INTO the six /home/lumen/bin rotate-* scripts listed above needs a worktree of
the bin repo, which this ticket was not dispatched with — see this directory's
README.md for the follow-up.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

BW = os.environ.get("ROTATION_GATE_BW", "/home/lumen/.npm-global/bin/bw")
LOG_PATH = os.environ.get("ROTATION_GATE_LOG", "/home/lumen/logs/rotation-safety-gate.log")


def fingerprint(value):
    """sha256[:12] of value — never log or print the value itself."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _backup_name_prefix(name):
    return f"rotation-backup/{name}/"


def _bw(args, timeout=20):
    try:
        r = subprocess.run([BW] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def vault_backup_value(name):
    """Most recently dated Vault backup note for `name`, or None on any failure
    (locked, missing, no bw binary, malformed response) — never raises."""
    if "BW_SESSION" not in os.environ:
        return None
    rc, out = _bw(["list", "items", "--search", _backup_name_prefix(name)])
    if rc != 0 or not out.strip():
        return None
    try:
        items = json.loads(out)
    except ValueError:
        return None
    prefix = _backup_name_prefix(name)
    candidates = sorted((it for it in items if it.get("name", "").startswith(prefix)),
                        key=lambda it: it.get("name", ""))
    if not candidates:
        return None
    rc, notes = _bw(["get", "notes", candidates[-1]["id"]])
    if rc != 0:
        return None
    return notes.rstrip("\n")


def require_backup(name, old_value):
    """True iff a Vault backup of `name` exists and its value matches old_value.
    Compares fingerprints only in-process; never prints either value."""
    if not old_value:
        return False
    backup = vault_backup_value(name)
    if backup is None:
        return False
    return fingerprint(backup) == fingerprint(old_value)


def log_rotation(script, name, old_value, new_value, status):
    """Append one line: ts, script, credential name, status, sha256[:12] of
    old/new. Never the plaintext value."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "script": script,
        "name": name,
        "status": status,
        "old_fp": fingerprint(old_value) if old_value else None,
        "new_fp": fingerprint(new_value) if new_value else None,
    }
    fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(entry) + "\n")


def require_backup_or_refuse(script, name, old_value):
    """require_backup, plus the refusal message + log entry a rotation script's
    gate call wants. Returns True iff the rotation may proceed."""
    if require_backup(name, old_value):
        return True
    log_rotation(script, name, old_value, None, "refused_no_vault_backup")
    print(f"{script}: REFUSING to rotate '{name}' — no Vault backup of the current value found "
          f"under rotation-backup/{name}/<date> (or BW_SESSION not unlocked in this shell). "
          f"Run: python3 vault_rotation_backup.py {name}   (in this same shell), then retry.",
          file=sys.stderr)
    return False


def _cli(argv):
    if not argv:
        print("usage: rotation_vault_gate.py {require-backup NAME SCRIPT|log SCRIPT NAME STATUS}", file=sys.stderr)
        return 64
    cmd = argv[0]
    if cmd == "require-backup" and len(argv) >= 3:
        name, script = argv[1], argv[2]
        old = sys.stdin.read()
        return 0 if require_backup_or_refuse(script, name, old) else 1
    if cmd == "log" and len(argv) >= 4:
        script, name, status = argv[1], argv[2], argv[3]
        lines = sys.stdin.read().split("\n", 1)
        old = lines[0] if lines and lines[0] else None
        new = lines[1].rstrip("\n") if len(lines) > 1 and lines[1] else None
        log_rotation(script, name, old, new, status)
        return 0
    print(f"unrecognized invocation: {argv}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
