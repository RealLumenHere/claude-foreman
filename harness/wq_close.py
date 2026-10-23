"""wq_close.py — close a ticket you do not hold, and report the verdict the queue actually
reached (wq#1479).

The incident: on 2026-09-07 an orchestrator session ran `wq done --no-artifact` on the
frank-cred tickets wq#1133 and wq#1296, read `tail -1` of the output, and reported both
closed. Both were still `blocked`, `done_at` NULL. `wq done` had refused: exit 1, and its
REFUSED text printed on stdout, where the last line of a multi-line refusal ("...there is
deliberately no --force.") reads exactly like an ordinary trailing note.

Two things were wrong and this module fixes both:

  1. The verdict was never read. `close()` gates on the exit code of every step and, after
     the close, re-reads `wq show` — the ticket's own status is the verdict, not anything
     the CLI printed. A close is reported only when the row says `done`.
  2. The verdict was not the last line. Every run ends with exactly one machine-readable
     line, `CLOSED wq#N` or `NOT-CLOSED wq#N step=... rc=...: <first refusal line>`, so
     `tail -1` is correct by construction rather than by luck.

It also owns the claim-then-done sequence, which is not obvious: `wq done` on a ticket
held by ANOTHER live session is refused by owner_ok, and `wq claim --id` refuses a
`blocked` ticket outright (wq:1245 — "cannot claim wq#N by id — BLOCKED"). Closing a
blocked frank-gated ticket therefore means unblock -> claim -> done. If any step after
the unblock fails, the original block is restored: a wrapper that leaves a frank-gated
ticket sitting `open` because its close failed has quietly widened the queue.

This is the close path the Liveness Gate's Slice 3 must use (research/LIVENESS_GATE.md §10).

CLI:
    python3 harness/wq_close.py 1133 --notes "..." --no-artifact "liveness: <detail>"
    python3 harness/wq_close.py 1133 --notes "..." --json
Exit 0 only when the ticket is `done` on the record.
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys

WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")

# wq#4577: the staging pattern (wq#4537/#4539/#4540) is correct doctrine — a worker walled
# out of a live path builds+tests against fixtures and ships ops/<ticket>/install.sh for a
# session that holds the grant. The hole is the handoff: wq#4540 closed `done` this way with
# nothing filing the deploy, and its own acceptance probe sat unmeasured for 3h until a
# verify pass caught it (ISSUE-LOG 1537). These two shapes are the textual admission that
# notes carry when a close is actually a handoff, not a deploy.
STAGED_INSTALL_PATH_RE = re.compile(r"ops/(?:wq#?)?\d+/install\.sh", re.I)
STAGED_INSTALL_SHAPE_RE = re.compile(r"staged\b.{0,60}\b(?:unreachable|no fs access)", re.I | re.S)


def staged_install_successor(ticket_id, notes, db_path=None):
    """Detect the staged-for-another-session admission in closing `notes` and look for a
    successor ticket whose own --deps names this ticket. Returns (triggered, successor_id):
    `triggered` is True when the notes carry the admission; `successor_id` is the id of a
    ticket that deps-links `ticket_id`, or None when none does (or the queue db can't be
    read — fail toward "no successor found", since that is the refusable state, same as the
    wq#899 merge-state gate failing toward "not merged" on ambiguity for a textual check this
    narrow). Detection only — the caller decides what to do with it, same split as
    `wq.py`'s own `pr_merge_state()`/cmd_done gate pairing."""
    text = notes or ""
    if not (STAGED_INSTALL_PATH_RE.search(text) or STAGED_INSTALL_SHAPE_RE.search(text)):
        return False, None
    sid = str(int(ticket_id))
    try:
        con = sqlite3.connect(db_path or WQ_DB)
        try:
            rows = con.execute(
                "SELECT id, deps FROM tasks WHERE deps IS NOT NULL AND deps != ''").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return True, None
    for rid, deps in rows:
        for clause in [c.strip() for c in (deps or "").split(",") if c.strip()]:
            low = clause.lower()
            d = clause[3:] if low.startswith("wq#") else clause[2:] if low.startswith("wq") else clause
            if d.strip() == sid:
                return True, rid
    return True, None


class Verdict:
    """closed is the answer; everything else is the evidence for it."""

    def __init__(self, ticket_id, closed, step, rc, refusal="", status=None, steps=None):
        self.ticket_id = int(ticket_id)
        self.closed = closed
        self.step = step
        self.rc = rc
        self.refusal = refusal
        self.status = status
        self.steps = steps or []

    @property
    def line(self):
        if self.closed:
            return f"CLOSED wq#{self.ticket_id}"
        return (f"NOT-CLOSED wq#{self.ticket_id} step={self.step} rc={self.rc}"
                f": {self.refusal or '(no refusal text)'}")

    def as_dict(self):
        return {"ticket": self.ticket_id, "closed": self.closed, "step": self.step,
                "rc": self.rc, "refusal": self.refusal, "status": self.status,
                "steps": self.steps, "verdict": self.line}


def _run(args, agent=None):
    env = dict(os.environ)
    if agent:
        env["WQ_AGENT"] = agent
    return subprocess.run([WQ_BIN] + [str(a) for a in args],
                          capture_output=True, text=True, env=env)


def _refusal_of(p):
    """The one line worth quoting. A refusal names itself REFUSED on some line of either
    stream; when nothing does, fall back to the first non-empty line so the caller is never
    handed an empty explanation."""
    for stream in (p.stderr, p.stdout):
        for line in stream.splitlines():
            if "REFUSED" in line:
                return line.strip()
    for stream in (p.stderr, p.stdout):
        for line in stream.splitlines():
            if line.strip():
                return line.strip()
    return ""


def show(ticket_id, agent=None):
    """`wq show <id>` as a dict. wq has no --json; this mirrors fworker.wq_show."""
    p = _run(["show", ticket_id], agent=agent)
    out = {}
    for line in p.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def _status(row):
    return (row.get("status") or "").strip()


def close(ticket_id, notes, no_artifact=None, agent=None, unmerged=None, log=None,
          staged_install_override=None):
    """Claim-then-done, gated on the exit code of every step and confirmed against the row.

    Returns a Verdict. Never raises on a refusal — a refusal is an answer, and the caller
    is expected to read it.
    """
    ticket_id = int(ticket_id)
    steps = []
    agent = agent or os.environ.get("WQ_AGENT", "").strip() or f"wq-close-{ticket_id}"

    def emit(msg):
        steps.append(msg)
        if log:
            log(msg)

    def fail(step, p):
        refusal = _refusal_of(p)
        emit(f"{step}: rc={p.returncode} {refusal}")
        return Verdict(ticket_id, False, step, p.returncode, refusal,
                       _status(show(ticket_id, agent=agent)), steps)

    row = show(ticket_id, agent=agent)
    if not row.get("id"):
        return Verdict(ticket_id, False, "show", 1, f"REFUSED: no such task {ticket_id}",
                       None, steps)
    status, holder = _status(row), (row.get("claimed_by") or "").strip()

    if status == "done":
        # Idempotent on purpose: a second `wq done` is REFUSED rc=4 to protect the first
        # close's notes (wq#1116), and reporting that refusal as a failure would be a lie
        # about the ticket, which is exactly the class of bug this module exists for.
        emit("show: already done — nothing to do")
        return Verdict(ticket_id, True, "show", 0, "", "done", steps)

    staged, successor = staged_install_successor(ticket_id, notes)
    if staged and not successor and not staged_install_override:
        refusal = (
            f"REFUSED: wq#4577 staged-install-successor gate — wq#{ticket_id}'s closing "
            "notes admit the deliverable is staged for another session (an "
            "ops/<ticket>/install.sh reference, or 'staged ... unreachable/no fs access') "
            "and no ticket's --deps names this close. File a successor ticket with "
            f"--deps {ticket_id} naming who deploys it, or record an explicit override "
            "with --staged-install-successor \"<reason>\" (wq#4573: wq#4540 closed this "
            "way with nothing filing the deploy, caught 3h later only by a human noticing).")
        emit(refusal)
        return Verdict(ticket_id, False, "staged-install-successor", 1, refusal, status, steps)
    if staged and successor:
        notes = (notes + (" " if notes else "") + f"[STAGED-SUCCESSOR: wq#{successor}]").strip()
        emit(f"staged-install-successor: wq#{successor} deps-links this close")
    elif staged and staged_install_override:
        notes = (notes + (" " if notes else "") +
                 f"[STAGED-INSTALL-OVERRIDE: {staged_install_override}]").strip()
        emit("staged-install-successor: override recorded on the record")

    reblock_reason = None
    if status == "blocked":
        reblock_reason = (row.get("block_reason") or row.get("notes") or
                          "re-blocked after a failed wq_close").strip()
        p = _run(["edit", ticket_id, "--status", "open"], agent=agent)
        if p.returncode != 0:
            return fail("unblock", p)
        emit("unblock: blocked -> open")

    def restore_block(v):
        """Anything short of `done` must leave the ticket where it was found."""
        if reblock_reason and not v.closed:
            r = _run(["block", ticket_id, reblock_reason], agent=agent)
            emit(f"restore-block: rc={r.returncode}")
        return v

    if holder and holder not in ("None", agent):
        p = _run(["release", ticket_id], agent=agent)
        if p.returncode != 0:
            return restore_block(fail("release", p))
        emit(f"release: dropped stale claim by '{holder}'")

    p = _run(["claim", "--id", ticket_id], agent=agent)
    if p.returncode != 0:
        return restore_block(fail("claim", p))
    emit(f"claim: held by {agent}")

    args = ["done", ticket_id, "--notes", notes]
    if no_artifact:
        args += ["--no-artifact", no_artifact]
    if unmerged:
        args += ["--unmerged", unmerged]
    p = _run(args, agent=agent)
    if p.returncode != 0:
        v = fail("done", p)
        _run(["release", ticket_id], agent=agent)
        return restore_block(v)

    # The verdict. Everything above is chatter; this is the queue's own answer.
    final = _status(show(ticket_id, agent=agent))
    if final != "done":
        v = Verdict(ticket_id, False, "confirm", 1,
                    f"REFUSED: `wq done` exited 0 but wq#{ticket_id} is status={final!r}",
                    final, steps)
        return restore_block(v)
    emit("confirm: status=done on the record")
    return Verdict(ticket_id, True, "confirm", 0, "", "done", steps)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Close a wq ticket you do not hold: claim-then-done, gated on the "
                    "exit code of every step and confirmed against the ticket row.",
        epilog="The last line of output is always the verdict: CLOSED / NOT-CLOSED.")
    ap.add_argument("id", type=int)
    ap.add_argument("--notes", required=True, help="closing notes (wq done --notes)")
    ap.add_argument("--no-artifact", dest="no_artifact",
                    help="reason, when the close has no file/PR/sha artifact (wq#759)")
    ap.add_argument("--unmerged", help="reason, when the evidence PR has not landed (wq#899)")
    ap.add_argument("--staged-install-successor", dest="staged_install_successor",
                    help="override, when the notes admit a staged-for-another-session "
                         "deliverable and no ticket deps-links this close (wq#4577)")
    ap.add_argument("--agent", help="wq identity to claim under (default: $WQ_AGENT)")
    ap.add_argument("--json", action="store_true", help="verdict as JSON on the last line")
    a = ap.parse_args(argv)

    v = close(a.id, a.notes, no_artifact=a.no_artifact, unmerged=a.unmerged, agent=a.agent,
              staged_install_override=a.staged_install_successor,
              log=None if a.json else lambda m: print(f"  {m}", file=sys.stderr))
    print(json.dumps(v.as_dict()) if a.json else v.line)
    return 0 if v.closed else 1


if __name__ == "__main__":
    sys.exit(main())
