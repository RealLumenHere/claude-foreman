#!/usr/bin/env python3
"""Stop hook — self-verify before the done call (ISSUE-SOLUTIONS-fable.md §1c).

Self-verify is not a prompt suggestion but a gate the worker physically cannot stop
without passing: if the ticket is not yet done and evidence.json is missing or incomplete
against the pinned acceptance.json, this prints the specific gap and blocks the stop.

Fail-OPEN by construction — no pinned acceptance, no ticket env, an unreadable run dir or
any unexpected error all allow the stop. The gate only ever fires on a ticket the harness
itself pinned criteria for. Capped at MAX_BLOCKS: after that the stop is allowed and
fworker's verify_done() routes the ticket to rework, so a confused worker can never loop
inside the hook forever.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import acceptance  # noqa: E402

MAX_BLOCKS = int(os.environ.get("FOREMAN_STOP_GATE_MAX_BLOCKS", "3"))
WQ = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")


def ticket_status(ticket_id):
    """`wq show <id>` status column; None on any failure (-> treated as not-done)."""
    try:
        p = subprocess.run([WQ, "show", str(ticket_id)],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        if line.startswith("status"):
            _, _, value = line.partition(":")
            return value.strip()
    return None


def bump_blocks(run_dir):
    path = os.path.join(run_dir, acceptance.BLOCKS_FILE)
    n = 0
    try:
        with open(path) as f:
            n = int(json.load(f).get("blocks", 0))
    except (OSError, ValueError, TypeError):
        n = 0
    n += 1
    try:
        with open(path, "w") as f:
            json.dump({"blocks": n}, f)
    except OSError:
        pass
    return n


def decide():
    """(block: bool, reason: str) — every non-gap path returns block=False."""
    ticket_id = os.environ.get(acceptance.TICKET_ENV)
    run_dir = os.environ.get(acceptance.RUN_DIR_ENV)
    worktree = os.environ.get("FOREMAN_WORKTREE_ROOT") or os.getcwd()
    if not ticket_id or not run_dir:
        return False, ""

    pinned = acceptance.load_pinned(run_dir)
    if not pinned or not pinned.get("criteria"):
        return False, ""

    if ticket_status(ticket_id) == "done":
        return False, ""

    gaps = acceptance.evidence_gaps(pinned, acceptance.load_evidence(worktree, run_dir))
    if not acceptance.worktree_copy_matches(run_dir, worktree):
        gaps.append(f"{acceptance.ACCEPTANCE_FILE} in the worktree no longer matches the "
                    f"pinned copy — restore it; it is read-only and the pinned copy governs")
    if not gaps:
        return False, ""

    n = bump_blocks(run_dir)
    if n > MAX_BLOCKS:
        return False, ""

    reason = "\n".join([
        f"Evidence gate (wq#{ticket_id}, block {n}/{MAX_BLOCKS}): you cannot stop yet — "
        f"the pinned acceptance criteria are not evidenced.",
        "",
        *[f"- {g}" for g in gaps],
        "",
        f"Fix the gap, then write {os.path.join(worktree, acceptance.EVIDENCE_FILE)} with "
        f"one entry per criterion (test criteria need cmd_run + exit_code, citation "
        f"criteria need \"path:line\"). The harness re-runs every command itself, so "
        f"run them for real first. Criteria: "
        f"{os.path.join(run_dir, acceptance.ACCEPTANCE_FILE)}",
    ])
    return True, reason


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    try:
        block, reason = decide()
    except Exception as e:
        print(f"stop_gate: fail-open ({e})", file=sys.stderr)
        return 0
    if block:
        print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
