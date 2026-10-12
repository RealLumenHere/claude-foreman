#!/usr/bin/env python3
"""vault_rotation_backup — attended-operator half of the wq#3747 safety gate.

Run this BEFORE a rotate-* tool, in the same shell, so rotation_vault_gate's
require_backup() has something to find. It:

  1. Unlocks/logs in to Vaultwarden via `bw` the same way vault-put-note does
     (hidden master-password prompt — never argv, never a saved file).
  2. Reads the OLD credential value from stdin (hidden prompt if a TTY).
  3. Stores it as a Vaultwarden secure note named rotation-backup/<name>/<date>.
  4. Writes a local freshness marker (fingerprint + timestamp, NOT the value)
     that harness/guard.sh checks before letting a headless worker invoke the
     matching rotate-* tool — see MARKER_DIR below and guard.sh's wq#3747 block.

Usage:
    python3 vault_rotation_backup.py <name>
    (paste the OLD credential value, Ctrl-D to finish)

This is deliberately interactive: a master password only an attended operator
can supply is the thing that makes the "refuses without a backup" gate
meaningful. A headless worker cannot run this step itself.
"""
import hashlib
import json
import os
import subprocess
import sys
import time

BW = os.environ.get("ROTATION_GATE_BW", "/home/lumen/.npm-global/bin/bw")
MARKER_DIR = os.environ.get("FOREMAN_VAULT_BACKUP_MARKER_DIR",
                            "/home/lumen/.secrets/.rotation-vault-backups")


def fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def bw(args, input_text=None, timeout=30):
    return subprocess.run([BW] + args, input=input_text, capture_output=True, text=True, timeout=timeout)


def ensure_unlocked():
    status = json.loads(bw(["status"]).stdout)["status"]
    if status == "unauthenticated":
        print("-- Vaultwarden login (email + master password) --", file=sys.stderr)
        session = bw(["login", "--raw"]).stdout.strip()
    elif status == "locked":
        print("-- Vaultwarden unlock (master password) --", file=sys.stderr)
        session = bw(["unlock", "--raw"]).stdout.strip()
    elif status == "unlocked":
        session = os.environ.get("BW_SESSION", "")
    else:
        sys.exit(f"unexpected bw status: {status}")
    if not session:
        sys.exit("bw did not return a session")
    os.environ["BW_SESSION"] = session
    bw(["sync"])
    return session


def write_marker(name, value):
    os.makedirs(MARKER_DIR, exist_ok=True)
    os.chmod(MARKER_DIR, 0o700)
    marker = os.path.join(MARKER_DIR, f"{name}.ok")
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "fingerprint": fingerprint(value)}, f)
    return marker


def main(argv):
    if len(argv) != 1:
        sys.exit("usage: vault_rotation_backup.py <name>  (OLD value on stdin, Ctrl-D to finish)")
    name = argv[0]
    ensure_unlocked()

    print("Paste the OLD credential value now. Finish with Ctrl-D on a blank line:", file=sys.stderr)
    value = sys.stdin.read().strip()
    if not value:
        print("empty input — nothing stored", file=sys.stderr)
        bw(["lock"])
        return 1

    item_name = f"rotation-backup/{name}/{time.strftime('%Y-%m-%d')}"
    payload = json.dumps({
        "organizationId": None, "collectionIds": None, "folderId": None,
        "type": 2, "name": item_name, "notes": value,
        "favorite": False, "reprompt": 0, "secureNote": {"type": 0},
    })
    encoded = bw(["encode"], input_text=payload).stdout
    create = bw(["create", "item"], input_text=encoded)
    if create.returncode != 0:
        print(f"bw create item failed: {create.stderr.strip()}", file=sys.stderr)
        bw(["lock"])
        return 1
    bw(["sync"])

    marker = write_marker(name, value)
    del value  # out of scope ASAP; nothing after this line needs it

    print(f"stored: {item_name}", file=sys.stderr)
    print(f"marker: {marker}", file=sys.stderr)
    bw(["lock"])
    os.environ.pop("BW_SESSION", None)
    print("vault locked.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
