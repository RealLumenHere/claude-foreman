#!/usr/bin/env python3
"""agent_kill.py — emergency kill switch for fleet agents (wq#4929).

Stops a specific agent, revokes its credentials, invalidates MCP sessions,
cancels queued work, releases leases, prevents restart, preserves audit
records, and quarantines the workspace — all in one atomic sequence.

Usage:
    python3 security/agent_kill.py --ticket <id> --reason <text> [--dry-run]
    python3 security/agent_kill.py --unkill --ticket <id> --reason <text>
"""

import argparse
import datetime
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time

def _foreman_root():
    return os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")

def _runs_root():
    return os.environ.get("FOREMAN_RUNS_ROOT", os.path.join(_foreman_root(), "_runs"))

def _worktrees_root():
    return os.environ.get("FOREMAN_WORKTREES_ROOT", "/home/lumen/worktrees")

def _wq_bin():
    return os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")

def _slock_bin():
    return os.environ.get("FOREMAN_SLOCK_BIN", "/home/lumen/bin/slock")

def _te_client():
    return os.path.join(_foreman_root(), "token-engine", "te_client.py")

def _incidents_dir():
    return os.environ.get(
        "AGENT_KILL_INCIDENTS_DIR",
        os.path.join(_foreman_root(), "security", "incidents"),
    )

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_chain import AuditChain


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid_file(ticket_id):
    return f"/tmp/fworker-wq{ticket_id}.pid"


def _kill_marker(ticket_id):
    return f"/tmp/fworker-wq{ticket_id}.killed"


def _read_pid(ticket_id):
    path = _pid_file(ticket_id)
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _find_worktrees(ticket_id):
    """Find all worktrees that might belong to this ticket."""
    pattern = os.path.join(_worktrees_root(), f"wq{ticket_id}-*")
    return glob.glob(pattern)


class KillSwitch:
    """Orchestrates the eight-step kill sequence."""

    def __init__(self, ticket_id, reason, dry_run=False, incidents_dir=None):
        self.ticket_id = ticket_id
        self.reason = reason
        self.dry_run = dry_run
        self.incidents_dir = incidents_dir or _incidents_dir()
        self.ts = now_iso()
        self.ts_compact = self.ts.replace(":", "").replace("-", "")[:15]
        self.incident_dir = os.path.join(
            self.incidents_dir, f"wq{ticket_id}-{self.ts_compact}"
        )
        self.worker_id = f"fworker-wq{ticket_id}"
        self.steps_completed = []
        self.chain = None

    def _init_chain(self):
        if self.dry_run:
            return
        os.makedirs(self.incident_dir, exist_ok=True)
        chain_path = os.path.join(self.incident_dir, "kill-log.jsonl")
        self.chain = AuditChain(chain_path)

    def _log(self, step, detail):
        if self.chain:
            self.chain.append(
                event="kill_step",
                detail={"step": step, **detail},
                worker_id=self.worker_id,
                ticket_id=self.ticket_id,
            )

    def _run_cmd(self, argv, timeout=30):
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout
            )
            return p.returncode, p.stdout.strip(), p.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"
        except FileNotFoundError:
            return -1, "", f"not found: {argv[0]}"

    def step1_stop_process(self):
        """Stop the agent's process tree."""
        pid = _read_pid(self.ticket_id)
        detail = {"pid": pid}

        if pid is None:
            detail["result"] = "no_pid_file"
            self._log("stop", detail)
            self.steps_completed.append("stop")
            return "no_pid_file", detail

        if not _pid_alive(pid):
            detail["result"] = "already_dead"
            self._log("stop", detail)
            self.steps_completed.append("stop")
            return "already_dead", detail

        if self.dry_run:
            detail["result"] = "would_kill"
            return "dry_run", detail

        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            detail["result"] = "vanished_before_pgid"
            self._log("stop", detail)
            self.steps_completed.append("stop")
            return "vanished", detail

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            detail["result"] = "vanished_after_sigterm"
            self._log("stop", detail)
            self.steps_completed.append("stop")
            return "vanished", detail

        deadline = time.time() + 30
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.5)

        if _pid_alive(pid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            time.sleep(1)
            detail["signal"] = "SIGKILL"
        else:
            detail["signal"] = "SIGTERM"

        detail["result"] = "killed" if not _pid_alive(pid) else "unkillable"
        self._log("stop", detail)
        self.steps_completed.append("stop")
        return detail["result"], detail

    def step2_revoke_credentials(self):
        """Revoke all provider credentials minted for this ticket."""
        detail = {}
        sock_path = os.environ.get("TOKEN_ENGINE_SOCK", "/run/token-engine/broker.sock")

        if not os.path.exists(sock_path):
            detail["result"] = "broker_not_running"
            self._log("revoke", detail)
            self.steps_completed.append("revoke")
            return "broker_not_running", detail

        if not os.path.exists(_te_client()):
            detail["result"] = "te_client_not_found"
            self._log("revoke", detail)
            self.steps_completed.append("revoke")
            return "te_client_not_found", detail

        if self.dry_run:
            detail["result"] = "would_revoke"
            return "dry_run", detail

        rc, out, err = self._run_cmd([
            "python3", _te_client(), "--verb", "ticket-end",
            "--ticket", str(self.ticket_id),
            "--worker", self.worker_id,
        ])
        detail["rc"] = rc
        detail["result"] = "revoked" if rc == 0 else "revoke_failed"
        if err:
            detail["stderr"] = err[:200]
        self._log("revoke", detail)
        self.steps_completed.append("revoke")
        return detail["result"], detail

    def step3_invalidate_mcp(self):
        """Write a kill marker that guard.sh checks on every tool call."""
        marker = _kill_marker(self.ticket_id)
        detail = {"marker": marker}

        if os.path.exists(marker):
            detail["result"] = "already_marked"
            self._log("invalidate_mcp", detail)
            self.steps_completed.append("invalidate_mcp")
            return "already_marked", detail

        if self.dry_run:
            detail["result"] = "would_write_marker"
            return "dry_run", detail

        marker_data = {
            "ticket_id": self.ticket_id,
            "killed_at": self.ts,
            "killed_by": "agent-kill",
            "reason": self.reason,
        }
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(marker_data, f, indent=2)

        detail["result"] = "marker_written"
        self._log("invalidate_mcp", detail)
        self.steps_completed.append("invalidate_mcp")
        return "marker_written", detail

    def step4_cancel_queued_work(self):
        """Block the ticket in the work queue."""
        detail = {}

        if self.dry_run:
            detail["result"] = "would_block"
            return "dry_run", detail

        note = f"KILLED by agent-kill: {self.reason}"
        rc, out, err = self._run_cmd([_wq_bin(), "block", str(self.ticket_id),
                                       "--notes", note])
        detail["rc"] = rc
        if rc == 0:
            detail["result"] = "blocked"
        elif "already" in (out + err).lower():
            detail["result"] = "already_blocked"
        else:
            detail["result"] = "block_failed"
            detail["stderr"] = err[:200]
        self._log("cancel", detail)
        self.steps_completed.append("cancel")
        return detail["result"], detail

    def step5_release_leases(self):
        """Release slock leases and spawn reservations held by this worker."""
        detail = {}
        holder = self.worker_id

        if self.dry_run:
            detail["result"] = "would_release"
            return "dry_run", detail

        if os.path.exists(_slock_bin()):
            rc, out, err = self._run_cmd(
                [_slock_bin(), "release-all", "--holder", holder], timeout=15
            )
            detail["slock_rc"] = rc
            detail["slock_result"] = "released" if rc == 0 else "release_failed"
        else:
            detail["slock_result"] = "slock_not_found"

        spawn_res = f"/tmp/fworker-wq{self.ticket_id}.spawn"
        if os.path.exists(spawn_res):
            try:
                os.unlink(spawn_res)
                detail["spawn_reservation"] = "removed"
            except OSError as e:
                detail["spawn_reservation"] = f"remove_failed: {e}"
        else:
            detail["spawn_reservation"] = "none"

        detail["result"] = "released"
        self._log("release", detail)
        self.steps_completed.append("release")
        return "released", detail

    def step6_prevent_restart(self):
        """Write a persistent kill marker that fworker and dispatcher check."""
        marker = _kill_marker(self.ticket_id)
        detail = {"marker": marker}

        if os.path.exists(marker):
            try:
                with open(marker) as f:
                    data = json.load(f)
                if "steps_completed" not in data:
                    data["steps_completed"] = self.steps_completed[:]
                    with open(marker, "w") as f:
                        json.dump(data, f, indent=2)
            except (json.JSONDecodeError, OSError):
                pass
            detail["result"] = "marker_exists"
            self._log("prevent_restart", detail)
            self.steps_completed.append("prevent_restart")
            return "marker_exists", detail

        if self.dry_run:
            detail["result"] = "would_write"
            return "dry_run", detail

        detail["result"] = "marker_written_in_step3"
        self._log("prevent_restart", detail)
        self.steps_completed.append("prevent_restart")
        return "marker_written_in_step3", detail

    def step7_preserve_audit(self):
        """Snapshot run records and events for the incident."""
        detail = {}
        run_dir = os.path.join(_runs_root(), f"wq{self.ticket_id}")

        if self.dry_run:
            detail["result"] = "would_snapshot"
            detail["sources"] = [run_dir]
            return "dry_run", detail

        os.makedirs(self.incident_dir, exist_ok=True)

        if os.path.isdir(run_dir):
            dst = os.path.join(self.incident_dir, f"runs-wq{self.ticket_id}")
            if not os.path.exists(dst):
                shutil.copytree(run_dir, dst, dirs_exist_ok=True)
                detail["runs_copied"] = True
            else:
                detail["runs_copied"] = "already_exists"
        else:
            detail["runs_copied"] = "no_run_dir"

        events_dir = os.path.join(_foreman_root(), "events")
        worker_events = os.path.join(events_dir, f"{self.worker_id}.jsonl")
        if os.path.exists(worker_events):
            dst = os.path.join(self.incident_dir, f"{self.worker_id}.events.jsonl")
            if not os.path.exists(dst):
                shutil.copy2(worker_events, dst)
                detail["events_copied"] = True
            else:
                detail["events_copied"] = "already_exists"

        detail["result"] = "preserved"
        detail["incident_dir"] = self.incident_dir
        self._log("preserve", detail)
        self.steps_completed.append("preserve")
        return "preserved", detail

    def step8_quarantine_workspace(self):
        """Lock down the agent's worktree to prevent reads/writes."""
        detail = {}
        worktrees = _find_worktrees(self.ticket_id)

        if not worktrees:
            detail["result"] = "no_worktrees_found"
            self._log("quarantine", detail)
            self.steps_completed.append("quarantine")
            return "no_worktrees_found", detail

        if self.dry_run:
            detail["result"] = "would_quarantine"
            detail["worktrees"] = worktrees
            return "dry_run", detail

        quarantined = []
        for wt in worktrees:
            q_marker = os.path.join(wt, ".quarantined")
            if os.path.exists(q_marker):
                quarantined.append({"path": wt, "status": "already_quarantined"})
                continue
            try:
                with open(q_marker, "w") as f:
                    json.dump({
                        "ticket_id": self.ticket_id,
                        "quarantined_at": self.ts,
                        "reason": self.reason,
                    }, f, indent=2)
                os.chmod(wt, 0o000)
                quarantined.append({"path": wt, "status": "quarantined"})
            except OSError as e:
                quarantined.append({"path": wt, "status": f"failed: {e}"})

        detail["worktrees"] = quarantined
        detail["result"] = "quarantined"
        self._log("quarantine", detail)
        self.steps_completed.append("quarantine")
        return "quarantined", detail

    def execute(self):
        """Run all eight steps in sequence. Returns summary dict."""
        self._init_chain()

        if self.chain:
            self.chain.append(
                event="kill_initiated",
                detail={"reason": self.reason, "dry_run": self.dry_run},
                worker_id=self.worker_id,
                ticket_id=self.ticket_id,
            )

        results = {}
        steps = [
            ("stop", self.step1_stop_process),
            ("revoke", self.step2_revoke_credentials),
            ("invalidate_mcp", self.step3_invalidate_mcp),
            ("cancel", self.step4_cancel_queued_work),
            ("release", self.step5_release_leases),
            ("prevent_restart", self.step6_prevent_restart),
            ("preserve", self.step7_preserve_audit),
            ("quarantine", self.step8_quarantine_workspace),
        ]

        for name, step_fn in steps:
            try:
                result, detail = step_fn()
                results[name] = {"status": result, "detail": detail}
            except Exception as e:
                results[name] = {"status": "error", "detail": str(e)}
                if self.chain:
                    self.chain.append(
                        event="kill_step_error",
                        detail={"step": name, "error": str(e)},
                        worker_id=self.worker_id,
                        ticket_id=self.ticket_id,
                    )

        if self.chain:
            self.chain.append(
                event="kill_completed",
                detail={
                    "steps": {k: v["status"] for k, v in results.items()},
                    "dry_run": self.dry_run,
                },
                worker_id=self.worker_id,
                ticket_id=self.ticket_id,
            )

        marker = _kill_marker(self.ticket_id)
        if not self.dry_run and os.path.exists(marker):
            try:
                with open(marker) as f:
                    data = json.load(f)
                data["steps_completed"] = self.steps_completed
                with open(marker, "w") as f:
                    json.dump(data, f, indent=2)
            except (json.JSONDecodeError, OSError):
                pass

        return {
            "ticket_id": self.ticket_id,
            "worker_id": self.worker_id,
            "killed_at": self.ts,
            "reason": self.reason,
            "dry_run": self.dry_run,
            "incident_dir": self.incident_dir if not self.dry_run else None,
            "steps": results,
        }


def unkill(ticket_id, reason):
    """Reverse a kill: remove marker, restore worktree permissions, log to chain."""
    marker = _kill_marker(ticket_id)
    ts = now_iso()
    ts_compact = ts.replace(":", "").replace("-", "")[:15]
    worker_id = f"fworker-wq{ticket_id}"

    inc_dir = _incidents_dir()
    incident_dirs = sorted(glob.glob(
        os.path.join(inc_dir, f"wq{ticket_id}-*")
    ))
    chain_path = None
    if incident_dirs:
        chain_path = os.path.join(incident_dirs[-1], "kill-log.jsonl")
    else:
        os.makedirs(inc_dir, exist_ok=True)
        inc = os.path.join(inc_dir, f"wq{ticket_id}-{ts_compact}")
        os.makedirs(inc, exist_ok=True)
        chain_path = os.path.join(inc, "kill-log.jsonl")

    chain = AuditChain(chain_path)
    results = {}

    if os.path.exists(marker):
        os.unlink(marker)
        results["marker"] = "removed"
    else:
        results["marker"] = "not_present"

    worktrees = _find_worktrees(ticket_id)
    for wt in worktrees:
        q_marker = os.path.join(wt, ".quarantined")
        try:
            os.chmod(wt, 0o755)
            if os.path.exists(q_marker):
                os.unlink(q_marker)
            results.setdefault("worktrees", []).append(
                {"path": wt, "status": "restored"}
            )
        except OSError as e:
            results.setdefault("worktrees", []).append(
                {"path": wt, "status": f"failed: {e}"}
            )

    chain.append(
        event="unkill",
        detail={"reason": reason, "results": results},
        worker_id=worker_id,
        ticket_id=ticket_id,
    )

    return results


def is_killed(ticket_id):
    """Check if a ticket has been killed. Returns the marker data or None."""
    marker = _kill_marker(ticket_id)
    if not os.path.exists(marker):
        return None
    try:
        with open(marker) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"ticket_id": ticket_id, "killed_at": "unknown"}


def main():
    ap = argparse.ArgumentParser(
        description="Emergency kill switch for fleet agents"
    )
    ap.add_argument("--ticket", type=int, required=True,
                    help="Ticket ID of the agent to kill")
    ap.add_argument("--reason", type=str, default="manual kill",
                    help="Reason for the kill (recorded in audit log)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would happen without taking action")
    ap.add_argument("--unkill", action="store_true",
                    help="Reverse a previous kill")
    ap.add_argument("--check", action="store_true",
                    help="Check if a ticket is killed")
    args = ap.parse_args()

    if args.check:
        data = is_killed(args.ticket)
        if data:
            print(json.dumps(data, indent=2))
            sys.exit(0)
        else:
            print(f"wq#{args.ticket} is not killed")
            sys.exit(1)

    if args.unkill:
        results = unkill(args.ticket, args.reason)
        print(json.dumps(results, indent=2))
        return

    ks = KillSwitch(args.ticket, args.reason, dry_run=args.dry_run)
    summary = ks.execute()
    print(json.dumps(summary, indent=2))

    if not args.dry_run:
        failures = [k for k, v in summary["steps"].items()
                    if v["status"] == "error"]
        if failures:
            print(f"\nWARNING: {len(failures)} step(s) failed: {failures}",
                  file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
