#!/usr/bin/env python3
"""Stop hook — self-verify before done is a gate, not a suggestion.

ISSUE-SOLUTIONS-fable.md §1c (ISSUE-LOG #17). Wired from worker-settings.json's `Stop`
block. Contract: hook JSON on stdin, exit 0 = let the session end, exit 2 = block with
the reason on stderr (Claude Code feeds it back to the model).

Fail-OPEN, deliberately, and unlike guard.sh's fail-closed PreToolUse posture: this hook
is a convergence aid, not a safety wall. A worker that slips past it still cannot reach
a surviving `done` — fworker's verify_done() replays the same evidence after the child
exits and routes an unproven claim to rework. Wedging every session on a bug in this
file would cost far more than the blocks it would buy.

Blocks are capped (MAX_BLOCKS): a confused worker gets three specific, actionable nudges
and is then allowed to stop, so it can never loop inside the hook.
"""
import json
import os
import subprocess
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.dirname(HOOKS_DIR)
sys.path.insert(0, HARNESS_DIR)

import acceptance  # noqa: E402

MAX_BLOCKS = 3
WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
TICKET_ENV = "FOREMAN_TICKET_ID"
STATE_ENV = "FOREMAN_STOP_GATE_STATE"
ALLOW, BLOCK = 0, 2


def _ticket_is_done(ticket_id):
    if not ticket_id:
        return False
    try:
        p = subprocess.run([WQ_BIN, "show", str(ticket_id)],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    for line in p.stdout.splitlines():
        if line.startswith("status") and ":" in line:
            return line.split(":", 1)[1].strip() == "done"
    return False


def _bump_blocks(state_path, session_id):
    """Count blocks per session so the cap survives across hook invocations."""
    if not state_path:
        return 1
    state = acceptance.read_json(state_path) or {}
    key = session_id or "session"
    state[key] = int(state.get(key, 0)) + 1
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w") as f:
            json.dump(state, f)
    except OSError:
        pass
    return state[key]


def decide(hook_input):
    """(exit_code, message). Pure enough to test without a subprocess."""
    worktree = (os.environ.get("FOREMAN_WORKTREE_ROOT")
                or hook_input.get("cwd") or os.getcwd())
    ticket_id = os.environ.get(TICKET_ENV)
    # wq#1905: prefer the run_dir copy pin() wrote at dispatch and never touches again —
    # unlike evidence.json (attempt-scoped, only ever a live snapshot), the same doc
    # governs every attempt, so there is no staleness risk in reading it here. Falls
    # back to the worktree copy when run_dir isn't reachable (most workers' sandbox) or
    # the ticket id isn't set (e.g. a bare `decide()` call in tests).
    run_dir = acceptance.run_dir_path(ticket_id) if ticket_id else None
    acc = acceptance.read_acceptance(worktree, run_dir=run_dir)
    if not acc or not (acc.get("criteria") or []):
        return ALLOW, ""  # no pinned criteria for this ticket — nothing to enforce

    evidence = acceptance.read_evidence(worktree)
    missing = acceptance.gaps(acc, evidence)
    if not missing:
        return ALLOW, ""
    if _ticket_is_done(ticket_id):
        # the row is already done; fworker's verify_done() owns the verdict from here,
        # and blocking a session whose ticket has closed helps nobody.
        return ALLOW, ""

    n = _bump_blocks(os.environ.get(STATE_ENV), hook_input.get("session_id"))
    if n > MAX_BLOCKS:
        return ALLOW, ""

    ev_path = os.path.join(worktree, acceptance.EVIDENCE_FILENAME)
    lines = [
        f"STOP BLOCKED ({n}/{MAX_BLOCKS}) — evidence gate: you have not proven this "
        f"ticket done. Write {ev_path} before you stop.",
        "Unproven criteria:",
    ]
    for g in missing:
        lines.append(f"  [{g['id']}] {g['need']}")
    by_id = {str(c.get("id")): c for c in acc.get("criteria", [])}
    lines.append("What each one needs:")
    for g in missing:
        c = by_id.get(g["id"], {})
        if c.get("proof_type") == "test":
            lines.append(f"  [{g['id']}] run `{c.get('cmd')}` and record "
                         f'{{"id": "{g["id"]}", "cmd_run": "{c.get("cmd")}", "exit_code": <exit>}}')
        else:
            lines.append(f"  [{g['id']}] record {{\"id\": \"{g['id']}\", "
                         '"citation": "path/to/file.py:12-30"} — a real line range in a '
                         "file this branch touches")
    lines.append("The harness re-runs these itself after you exit; a claim it cannot "
                 "reproduce routes the ticket to rework, not done.")
    if n == MAX_BLOCKS:
        lines.append("This is the last block — the next stop is allowed and the harness "
                     "will judge the evidence as it stands.")
    return BLOCK, "\n".join(lines)


def main():
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
        if not isinstance(hook_input, dict):
            hook_input = {}
        if hook_input.get("stop_hook_active"):
            # already inside a hook-driven continuation; the counter below would still
            # cap it, but honoring the flag keeps us out of Claude Code's loop path.
            return ALLOW
        code, msg = decide(hook_input)
    except Exception as e:  # fail-open: see the module docstring
        print(f"stop_gate: internal error ({e}) — allowing stop", file=sys.stderr)
        return ALLOW
    if code == BLOCK:
        print(msg, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
