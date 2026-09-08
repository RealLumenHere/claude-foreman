#!/usr/bin/env python3
"""End-to-end proof of the wq#899 merge-state gate.

Runs the PATCHED `wq` as a real subprocess against a scratch queue DB and a fake forge
served from localhost, so every layer under test is the real one: argparse, cmd_done, the
HTTP call, and the git ancestry check. Nothing here touches the live queue, the live
/home/lumen/bin/wq, or the real Gitea.

    python3 ops/wq899-pr-recon/test_merge_state_gate.py

Exits 0 when every case passes. The binary under test is a copy of /home/lumen/bin/wq; if
that copy predates wq#905 the shipped wq899_merge_state_gate.diff is applied to it first,
which also proves the diff still applies. Point WQ_BIN at another `wq` to test that one.
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE_WQ = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
DIFF = os.path.join(HERE, "wq899_merge_state_gate.diff")


class Forge(http.server.BaseHTTPRequestHandler):
    """Serves whatever the current case put in Forge.payload, for any PR path."""
    payload = {}

    def do_GET(self):
        body = json.dumps(Forge.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


MARKER = "wq#899 merge-state gate"


def build_patched_wq(dest):
    """Copy LIVE_WQ to dest with the gate in it, patching only if it isn't there yet.

    Before wq#905 the gate lived only in this diff, so applying it was the whole setup step.
    Now it is in `wq` itself, and re-applying would fail — so the patch runs only against a
    base that predates the landing, which keeps this usable as a regression test either way.
    """
    shutil.copy(LIVE_WQ, dest)
    os.chmod(dest, 0o755)
    with open(dest, encoding="utf-8", errors="replace") as fh:
        if MARKER in fh.read():
            return
    p = subprocess.run(["patch", "-s", "--forward", dest, DIFF], capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit(f"the shipped diff no longer applies to {LIVE_WQ}:\n{p.stdout}{p.stderr}")
    os.chmod(dest, 0o755)


def main():
    if not os.path.exists(DIFF):
        sys.exit(f"missing {DIFF}")
    tmp = tempfile.mkdtemp(prefix="wq899-gate-")
    wq = os.path.join(tmp, "wq")
    build_patched_wq(wq)

    tokfile = os.path.join(tmp, "token")
    with open(tokfile, "w") as fh:
        fh.write("dummy-token-for-the-fake-forge\n")

    srv = http.server.HTTPServer(("127.0.0.1", 0), Forge)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    dead_port = 1  # nothing listens here: stands in for "the forge is down"

    env = dict(os.environ,
               WQ_DB=os.path.join(tmp, "queue.db"),
               GITEA_TOKEN_FILE=tokfile,
               WQ_AGENT="wq899-gate-test",
               WQ_MERGE_STATE_TIMEOUT="3")

    def run(*args, agent="wq899-gate-test"):
        return subprocess.run([sys.executable, wq, *args], capture_output=True, text=True,
                              env=dict(env, WQ_AGENT=agent), cwd=HERE)

    # A refused `done` deliberately leaves the ticket claimed, so each case gets its own
    # holder rather than fighting wq's one-live-claim-per-agent rule.
    holder = iter(f"wq899-gate-test-{i}" for i in range(1, 99))

    def fresh_ticket():
        who = next(holder)
        # unique title per case: `wq add` collapses duplicate titles onto the existing ticket.
        # lane t1: wq#912 refuses `add` on a lane with no dispatcher/config.json entry, and t1
        # is one of the three deliberately-exempt lanes. The scratch WQ_DB is per-run, so
        # nothing here reaches the real t1 queue.
        r = run("add", "--lane", "t1", "--title", f"gate case {who}", "--pri", "5")
        tid = next((t for t in reversed(r.stdout.split()) if t.isdigit()), "")
        if not tid or not r.stdout.startswith("added"):
            sys.exit(f"could not create a scratch ticket: {r.stdout}{r.stderr}")
        c = run("claim", who, "--lane", "t1", "--id", tid, agent=who)
        if "claimed" not in (c.stdout + c.stderr).lower():
            sys.exit(f"could not claim scratch ticket {tid}: {c.stdout}{c.stderr}")
        return tid, who

    def close(*extra):
        """Create a ticket, try to close it, and hand back (result, ticket-id)."""
        tid, who = fresh_ticket()
        return run("done", tid, *extra, agent=who), tid

    on_main = subprocess.run(["git", "-C", HERE, "rev-parse", "origin/main"],
                             capture_output=True, text=True).stdout.strip()
    off_main = "0" * 40  # a sha no repo has

    failures = []

    def check(name, cond, detail=""):
        print(f"{'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(f"{name}: {detail[:400]}")

    def url(port, n=7):
        return f"http://127.0.0.1:{port}/lumen/claude-foreman/pulls/{n}"

    # 1. merged PR — the happy path the gate must not disturb.
    Forge.payload = {"state": "closed", "merged": True, "head": {"sha": off_main},
                     "base": {"ref": "main"}}
    r, _ = close("--notes", f"PR {url(port)}")
    check("merged PR closes the ticket", r.returncode == 0, r.stdout + r.stderr)

    # 2. THE BUG wq#899 EXISTS FOR: open PR, head nowhere near main.
    Forge.payload = {"state": "open", "merged": False, "head": {"sha": off_main},
                     "base": {"ref": "main"}}
    r, _ = close("--notes", f"PR {url(port)}")
    # wq#1479 moved close-path refusals to stderr, so the assertion reads both streams.
    check("open unlanded PR is REFUSED", r.returncode == 3 and "wq#899" in r.stdout + r.stderr,
          f"rc={r.returncode} {r.stdout}{r.stderr}")

    # 3. open PR whose head IS on main — a squash or cherry-pick. Must not be refused.
    Forge.payload = {"state": "open", "merged": False, "head": {"sha": on_main},
                     "base": {"ref": "main"}}
    r, _ = close("--notes", f"PR {url(port)}")
    check("open PR already on main is accepted", r.returncode == 0 and "already on" in r.stdout,
          f"rc={r.returncode} {r.stdout}{r.stderr}")

    # 4. forge unreachable — FAIL OPEN. A gate that wedges the fleet on a network blip is worse
    #    than the bug it guards.
    r, _ = close("--notes", f"PR {url(dead_port)}")
    check("unreachable forge warns but allows", r.returncode == 0 and "WARNING" in r.stdout,
          f"rc={r.returncode} {r.stdout}{r.stderr}")

    # 5. the override exists, and using it leaves a mark on the ticket.
    Forge.payload = {"state": "open", "merged": False, "head": {"sha": off_main},
                     "base": {"ref": "main"}}
    r, tid = close("--notes", f"PR {url(port)}", "--unmerged", "stale ref, work is in ops/")
    shown = run("show", tid).stdout
    check("--unmerged closes and is recorded",
          r.returncode == 0 and "UNMERGED-PR" in shown, f"rc={r.returncode} {r.stdout}{shown}")

    # 6. wq#759 regression guard: a non-PR artifact must not acquire a forge dependency.
    r, _ = close("--notes", f"see {DIFF}")
    check("file-path artifact still closes without the forge",
          r.returncode == 0 and "wq#899" not in r.stdout + r.stderr,
          f"rc={r.returncode} {r.stdout}{r.stderr}")

    # 7. closed-without-merging is not evidence either.
    Forge.payload = {"state": "closed", "merged": False, "head": {"sha": off_main},
                     "base": {"ref": "main"}}
    r, _ = close("--notes", f"PR {url(port)}")
    check("PR closed without merging is REFUSED", r.returncode == 3, f"rc={r.returncode} {r.stdout}")

    # 8. wq#759 itself still bites: no artifact at all.
    r, _ = close("--notes", "trust me")
    check("wq#759 empty-artifact gate still bites", r.returncode == 2, f"rc={r.returncode} {r.stdout}")

    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("\n" + "\n".join(failures))
        sys.exit(1)
    print("\nall wq#899 merge-state gate cases pass")


if __name__ == "__main__":
    main()
