#!/usr/bin/env python3
"""wq#2567: deployed-vs-merged drift check for token_engine_daemon.py.

Third occurrence of this exact drift class (ISSUE-LOG rows 595/695/697/698):
token-engine.service runs the daemon from /usr/local/lib/token-engine, not
from the repo, so a merged fix is not automatically a deployed fix, and a
green client probe (`lumen-pr check`) passes identically against a pre-fix
daemon. Previously the only way to tell was forensic: compare systemctl
ActiveEnterTimestamp/MainPID/NRestarts against the fix commit time and grep
journalctl for restart lines.

This compares three copies of the file that must agree:
  - repo:      this checkout's token_engine_daemon.py
  - installed: /usr/local/lib/token-engine/token_engine_daemon.py, root-owned
    but world-readable (0755) — what install.sh last wrote
  - running:   the sha256 the live daemon reports over its own `ping` verb
    (self_sha256, wq#2567) — the source main() actually loaded, which lags
    `installed` if the daemon hasn't been restarted since the last install

Usage:
    check_deploy_drift.py [--src <repo-token-engine-dir>] [--ticket ID] [--worker NAME]

Exit 0 iff all three sha256 agree. Exit 1 and alert via ntfy on any mismatch,
exit 2 if the running daemon could not be reached at all.
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

INSTALLED = "/usr/local/lib/token-engine/token_engine_daemon.py"
SOCK_PATH = os.environ.get("TOKEN_ENGINE_SOCK", "/run/token-engine/broker.sock")
NTFY_BIN = "/home/lumen/bin/ntfy-send"
DEFAULT_SRC = "/home/lumen/foreman/token-engine"
# The daemon runs with PrivateTmp=true, so a grant under /tmp is invisible to
# it (gate_replay.sh hit this first) — mint it under the events tree instead,
# which install.sh already grants tokeneng traversal + rwX into.
GRANT_DIR_ROOT = "/home/lumen/foreman/events"


def sha256_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def ping_running_sha(ticket, worker, timeout=10):
    """Mints a throwaway {kind: token_engine, node: daemon} grant — same
    pattern as gate_replay.sh — and calls ping directly over the broker
    socket to read back self_sha256."""
    tmp = tempfile.mkdtemp(prefix="te-drift-check.", dir=GRANT_DIR_ROOT)
    try:
        os.chmod(tmp, 0o755)
        grant = {"ticket": ticket, "expires": "2099-01-01T00:00:00Z",
                 "grants": [{"kind": "token_engine", "node": "daemon"}]}
        raw = json.dumps(grant).encode()
        grant_path = os.path.join(tmp, "grant.json")
        with open(grant_path, "wb") as f:
            f.write(raw)
        os.chmod(grant_path, 0o444)
        grant_sha = hashlib.sha256(raw).hexdigest()
        req = {"verb": "ping", "argv": [], "ticket": ticket, "attempt": 1,
               "worker": worker, "grant_file": grant_path, "grant_sha": grant_sha}

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCK_PATH)
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        resp = json.loads(buf.split(b"\n", 1)[0].decode())
        if not resp.get("ok"):
            raise RuntimeError(f"ping denied: {resp.get('rule')} {resp.get('reason')}")
        return resp["result"].get("self_sha256")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def notify(title, message):
    """Best effort, same contract as the daemon's own notify()."""
    try:
        subprocess.run([NTFY_BIN, "-t", "lumen-alerts", "-p", "high", "-T", title,
                        message], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=DEFAULT_SRC,
                    help="repo token-engine dir (default: %(default)s)")
    ap.add_argument("--ticket", default="wq2567-drift-check")
    ap.add_argument("--worker", default="drift-check")
    args = ap.parse_args()

    repo_path = os.path.join(args.src, "token_engine_daemon.py")
    repo_sha = sha256_file(repo_path)
    installed_sha = sha256_file(INSTALLED)
    try:
        running_sha = ping_running_sha(args.ticket, args.worker)
    except (OSError, RuntimeError, ConnectionError) as e:
        print(f"DRIFT_CHECK_ERROR: could not reach running daemon at {SOCK_PATH}: {e}",
              file=sys.stderr)
        sys.exit(2)

    print(f"repo:      {repo_sha}  ({repo_path})")
    print(f"installed: {installed_sha}  ({INSTALLED})")
    print(f"running:   {running_sha}  (live ping)")

    if repo_sha and repo_sha == installed_sha == running_sha:
        print("OK: repo == installed == running")
        sys.exit(0)

    problems = []
    if repo_sha != installed_sha:
        problems.append(
            "repo != installed — install.sh has not been re-run since this file last changed")
    if installed_sha != running_sha:
        problems.append(
            "installed != running — token-engine.service has not been restarted since the last install")
    msg = "; ".join(problems) or "sha256 mismatch (repo or installed copy unreadable?)"
    print(f"DRIFT: {msg}")
    notify("token-engine deploy drift", msg)
    sys.exit(1)


if __name__ == "__main__":
    main()
