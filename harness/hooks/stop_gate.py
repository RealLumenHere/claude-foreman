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
import classifier  # noqa: E402 -- wq#1915(A): parse_worker_sentinel + sentinel set
import exit_gate  # noqa: E402 -- wq#5007: dual-condition exit gate

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
    transcript_path = hook_input.get("transcript_path")
    done = _ticket_is_done(ticket_id)

    # wq#1905: prefer the run_dir copy pin() wrote at dispatch and never touches again —
    # unlike evidence.json (attempt-scoped, only ever a live snapshot), the same doc
    # governs every attempt, so there is no staleness risk in reading it here. Falls
    # back to the worktree copy when run_dir isn't reachable (most workers' sandbox) or
    # the ticket id isn't set (e.g. a bare `decide()` call in tests).
    run_dir = acceptance.run_dir_path(ticket_id) if ticket_id else None
    acc = acceptance.read_acceptance(worktree, run_dir=run_dir)
    if acc and (acc.get("criteria") or []) and not done:
        evidence = acceptance.read_evidence(worktree)
        missing = acceptance.gaps(acc, evidence)
        if missing:
            n = _bump_blocks(os.environ.get(STATE_ENV), hook_input.get("session_id"))
            if n <= MAX_BLOCKS:
                return BLOCK, _acceptance_gap_message(n, worktree, missing, acc)
            # n > MAX_BLOCKS falls through to the mechanical traps below, same as the
            # acceptance path's own cap used to fall through to a bare ALLOW.

    # wq#1915: two mechanical traps, independent of acceptance criteria (a ticket with
    # none pinned -- the common case -- used to walk straight past the check above with
    # nothing left to enforce). Skipped once the ticket is done for the same reason the
    # acceptance path skips it: fworker's verify_done() owns the verdict from here, and
    # blocking a session whose ticket has already closed helps nobody.
    if not done:
        trap = _mechanical_trap(hook_input, worktree, transcript_path)
        if trap is not None:
            return trap

    gate = _exit_gate_block(hook_input, ticket_id, done, worktree, transcript_path)
    if gate is not None:
        return gate

    return ALLOW, ""


def _ticket_status(ticket_id):
    if not ticket_id:
        return None
    try:
        p = subprocess.run([WQ_BIN, "show", str(ticket_id)],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in p.stdout.splitlines():
        if line.startswith("status") and ":" in line:
            return line.split(":", 1)[1].strip()
    return None


def _branch_has_commits(worktree):
    try:
        p = subprocess.run(["git", "-C", worktree, "log", "--oneline", "-1",
                            "origin/main..HEAD"],
                           capture_output=True, text=True, timeout=15)
        return p.returncode == 0 and bool(p.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _exit_gate_block(hook_input, ticket_id, done, worktree, transcript_path):
    """wq#5007: the in-session half of the dual-condition exit gate — the worker is
    told at the moment it tries to stop, instead of being resumed afterwards. Only
    armed when fworker says this session's prompt carried the EXIT_SIGNAL contract
    (FOREMAN_EXIT_GATE_PROTOCOL=1). Shares the MAX_BLOCKS cap, fails open on an
    unreadable transcript, and steps aside for every legitimate non-completion
    stop: a BLOCKED/CLAIM_ERROR sentinel, or a ticket that has already left this
    worker's hands (released, blocked, parked)."""
    if os.environ.get("FOREMAN_EXIT_GATE_PROTOCOL") != "1" or exit_gate.mode() != "enforce":
        return None
    text = _transcript_last_assistant_text(transcript_path)
    if not text:
        return None
    sentinel = classifier.parse_worker_sentinel(text)
    if sentinel in (classifier.BLOCK_SENTINEL, classifier.CLAIM_ERROR_SENTINEL):
        return None
    if not done and _ticket_status(ticket_id) != "claimed":
        return None
    decision = exit_gate.decide({
        "ticket_done": done,
        "closed_sentinel": sentinel == classifier.TICKET_CLOSED_SENTINEL,
        "landed_commit": _branch_has_commits(worktree),
    }, exit_gate.parse_exit_signal(text))
    if decision.exit:
        return None
    n = _bump_blocks(os.environ.get(STATE_ENV), hook_input.get("session_id"))
    if n > MAX_BLOCKS:
        return None
    msg = f"STOP BLOCKED ({n}/{MAX_BLOCKS}) — " + exit_gate.nudge_text(decision, ticket_id)
    if n == MAX_BLOCKS:
        msg += ("\nThis is the last block — the next stop is allowed and the harness "
                "will judge the exit as it stands.")
    return BLOCK, msg


def _acceptance_gap_message(n, worktree, missing, acc):
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
    return "\n".join(lines)


def _transcript_last_assistant_text(transcript_path):
    """Concatenated text blocks of the LAST assistant message in a Claude Code session
    transcript (JSONL: one record per line, {"type": "assistant", "message": {"content":
    [...]}, ...}), or "" if the transcript is absent/unreadable. Matches what
    classifier.parse_worker_sentinel() already expects as "the model's final message" --
    fworker.py's own dispatch path builds that string from the child's captured
    stream-json frames; this is the interactive-session equivalent, read from the path
    Claude Code's Stop hook payload gives us (hook_input["transcript_path"]), not
    available to a dispatched (non-interactive) child at all. The transcript is this
    worker's own session, not attacker-controlled input, so no hardening beyond normal
    malformed-line tolerance is warranted here."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return ""
    last_text = ""
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                content = (rec.get("message") or {}).get("content") or []
                text = "".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                if text:
                    last_text = text
    except OSError:
        return ""
    return last_text


def _worktree_dirty_no_landing(worktree):
    """True iff the worktree has uncommitted tracked changes or untracked non-ignored
    files (git status --porcelain non-empty) AND no commit has landed on this branch
    yet (git log origin/main..HEAD empty) -- wq#1888 (238 turns, 11 modified + 3 new
    files) and wq#1905 (157 turns, 8 files) both stopped in exactly this state and went
    FALSE_COMPLETE on an empty branch diff. Fails OPEN (False) on any git trouble --
    this convergence aid must never block a stop over its own plumbing failing."""
    try:
        status = subprocess.run(["git", "-C", worktree, "status", "--porcelain"],
                                capture_output=True, text=True, timeout=15)
        if status.returncode != 0 or not status.stdout.strip():
            return False
        landed = subprocess.run(["git", "-C", worktree, "log", "--oneline",
                                 "origin/main..HEAD"],
                                capture_output=True, text=True, timeout=15)
        if landed.returncode != 0:
            return False
        return not landed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False


def _pending_background_task(transcript_path):
    """Best-effort: a Bash tool_use with run_in_background=true that the transcript
    never shows being polled to a terminal status via BashOutput. The hook has no live
    handle on Claude Code's own background-task list (it only ever sees this one Stop
    invocation's stdin), so per wq#1915's own fallback instruction this greps the
    transcript instead. Deliberately coarse -- ANY terminal-status poll anywhere in the
    transcript clears ALL pending background tool_use ids, rather than matching each
    poll to its own shell -- a convergence nudge false-negative here just means one
    fewer block message, never a wrongly blocked stop, and Claude Code's transcript
    does not expose shell ids on the BashOutput tool_result in a way worth the extra
    matching complexity for a fail-open hook."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return False
    saw_background_launch = False
    saw_terminal_poll = False
    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = (rec.get("message") or {}).get("content") or []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if (b.get("type") == "tool_use" and b.get("name") == "Bash"
                            and (b.get("input") or {}).get("run_in_background")):
                        saw_background_launch = True
                    if b.get("type") == "tool_result":
                        blob = json.dumps(b.get("content", ""))
                        if any(term in blob for term in
                               ('"status": "completed"', '"status": "failed"',
                                '"status": "killed"', "shell_id")):
                            saw_terminal_poll = True
    except OSError:
        return False
    return saw_background_launch and not saw_terminal_poll


def _mechanical_trap(hook_input, worktree, transcript_path):
    """wq#1915: the two mechanical traps, sharing the same MAX_BLOCKS counter the
    acceptance gate uses (one confused worker still gets 3 total nudges, not 3 per
    reason). Returns (BLOCK, msg) if either trap fires and the cap isn't exhausted,
    else None (caller falls through to ALLOW)."""
    sentinel_text = _transcript_last_assistant_text(transcript_path)
    sentinel = classifier.parse_worker_sentinel(sentinel_text)
    if sentinel is None and _worktree_dirty_no_landing(worktree):
        n = _bump_blocks(os.environ.get(STATE_ENV), hook_input.get("session_id"))
        if n <= MAX_BLOCKS:
            msg = (
                f"STOP BLOCKED ({n}/{MAX_BLOCKS}) — uncommitted work on the floor: "
                "this worktree has tracked or untracked changes and no commit has "
                "landed on this branch yet (git log origin/main..HEAD is empty). Run "
                "your tests in the foreground (not run_in_background), commit via "
                "`lumen-pr commit`, and `wq done` — do not stop to wait on a background "
                "task with nothing committed."
            )
            if n == MAX_BLOCKS:
                msg += ("\nThis is the last block — the next stop is allowed and the "
                        "harness will judge whatever is on disk as it stands.")
            return BLOCK, msg

    if _pending_background_task(transcript_path):
        n = _bump_blocks(os.environ.get(STATE_ENV), hook_input.get("session_id"))
        if n <= MAX_BLOCKS:
            msg = (
                f"STOP BLOCKED ({n}/{MAX_BLOCKS}) — a run_in_background command has no "
                "recorded completion in this transcript. Poll it with BashOutput to a "
                "terminal status before you stop, or re-run it in the foreground."
            )
            if n == MAX_BLOCKS:
                msg += ("\nThis is the last block — the next stop is allowed and the "
                        "harness will judge whatever is on disk as it stands.")
            return BLOCK, msg
    return None


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
