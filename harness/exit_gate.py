#!/usr/bin/env python3
"""wq#5007 — dual-condition exit gate, per-ticket circuit breaker, hourly call limit,
session expiry and per-loop git backup. Pattern port (not a code port) of
frankbria/ralph-claude-code; source notes: scratch/research/extract_ralph_claude_code.md,
design: design/WQ5007_EXIT_GATE.md.

What a "loop" is here: ONE child invocation (one do_attempt leg — a fresh spawn, a
steer/tool-ceiling/exit-nudge resume, or an attempt+1 resume). Ralph's loop is one
`claude` call; a leg is the same unit.

1. EXIT GATE. A worker's exit counts as a completion only when BOTH hold:
     completion_indicators >= 2   harness-observed facts, never prose
     EXIT_SIGNAL: true            explicit, in the worker's final message
   Missing EXIT_SIGNAL is false. Anything else continues the loop.
2. CIRCUIT BREAKER (per ticket, persisted, survives the fworker process):
     CLOSED -> HALF_OPEN -> OPEN; no-progress 3 loops, same error 5 loops, output
     decline > 70% on a loop that made no progress. OPEN recovers to HALF_OPEN after a
     30 min cooldown; one probe loop decides CLOSED (progress) or OPEN again.
     A loop that ended on a question / a parked hold holds the counters steady.
3. HOURLY LIMIT on child invocations, fleet-wide and per ticket.
4. SESSION EXPIRY: a session older than 24 h is not resumed (fresh session instead).
5. GIT BACKUP: a snapshot ref of the worktree before every loop, restorable.

Everything here is stdlib and side-effect-free on import. fworker wires it fail-OPEN to
the pre-existing behaviour: an exception in this module must never wedge a worker —
verify_done()/check_artifacts_done() remain the fail-closed wall behind it.
"""
import collections
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time


def _envint(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


MODE_ENV = "FOREMAN_EXIT_GATE"            # enforce (default) | shadow | off
PROTOCOL_MARKER = "EXIT_SIGNAL"
MIN_INDICATORS = _envint("FOREMAN_EXIT_MIN_INDICATORS", 2)

CB_NO_PROGRESS_THRESHOLD = _envint("FOREMAN_CB_NO_PROGRESS", 3)
CB_SAME_ERROR_THRESHOLD = _envint("FOREMAN_CB_SAME_ERROR", 5)
CB_OUTPUT_DECLINE_PCT = _envint("FOREMAN_CB_OUTPUT_DECLINE_PCT", 70)
CB_COOLDOWN_S = _envint("FOREMAN_CB_COOLDOWN_S", 30 * 60)
# A decline is only meaningful against a loop that actually said something.
CB_DECLINE_MIN_BASE_CHARS = _envint("FOREMAN_CB_DECLINE_MIN_BASE", 400)

MAX_CALLS_PER_HOUR = _envint("FOREMAN_MAX_CALLS_PER_HOUR", 100)
MAX_TICKET_CALLS_PER_HOUR = _envint("FOREMAN_MAX_TICKET_CALLS_PER_HOUR", 12)
SESSION_EXPIRY_S = _envint("FOREMAN_SESSION_EXPIRY_S", 24 * 3600)
BACKUP_KEEP = _envint("FOREMAN_LOOP_BACKUP_KEEP", 10)
BACKUP_REF_ROOT = "refs/foreman-backup"
# Untracked files are copied into the object store by a backup, so they are bounded:
# an RE worktree can hold multi-GB dumps that must never be hashed into .git on every
# loop. Tracked files are always included (they are already in the store).
BACKUP_MAX_UNTRACKED_FILE_BYTES = _envint("FOREMAN_LOOP_BACKUP_MAX_FILE", 5 * 1024 * 1024)
BACKUP_MAX_UNTRACKED_TOTAL_BYTES = _envint("FOREMAN_LOOP_BACKUP_MAX_TOTAL",
                                           100 * 1024 * 1024)

CLOSED, HALF_OPEN, OPEN = "CLOSED", "HALF_OPEN", "OPEN"

BREAKER_FILENAME = "circuit-breaker.json"
SESSIONS_FILENAME = "exit-gate-sessions.json"
CALLS_FILENAME = ".exit-gate-calls.json"


def mode():
    m = (os.environ.get(MODE_ENV) or "enforce").strip().lower()
    return m if m in ("enforce", "shadow", "off") else "enforce"


# --------------------------------------------------------------------------
# 1. exit gate
# --------------------------------------------------------------------------

# Line-anchored so prose ABOUT the protocol ("I will set EXIT_SIGNAL: true once…")
# inside a sentence does not count; markdown bullets/backticks around it are tolerated.
_SIGNAL_RE = re.compile(
    r"^[ \t>*\-`]*EXIT_SIGNAL[ \t]*[:=][ \t]*`?(true|false)\b`?[ \t.*`]*$",
    re.IGNORECASE | re.MULTILINE)


def parse_exit_signal(text):
    """True / False from the LAST `EXIT_SIGNAL: <bool>` line of the worker's final
    message; None when there is none (the gate reads None as false)."""
    if not isinstance(text, str):
        return None
    hits = _SIGNAL_RE.findall(text)
    if not hits:
        return None
    return hits[-1].lower() == "true"


Decision = collections.namedtuple("Decision", "exit indicators signal reason")


def decide(indicators, signal, min_indicators=None):
    """indicators: {name: bool} of harness-observed completion facts. signal: the
    parse_exit_signal() value. Exit only on >= min_indicators AND signal is True."""
    need = MIN_INDICATORS if min_indicators is None else min_indicators
    held = sorted(k for k, v in (indicators or {}).items() if v)
    if signal is True and len(held) >= need:
        return Decision(True, held, signal, "exit")
    if signal is False:
        return Decision(False, held, signal, "signal_false")
    if signal is None:
        return Decision(False, held, signal, "signal_missing")
    return Decision(False, held, signal, "indicators_below_threshold")


def nudge_text(decision, ticket_id=None):
    """The one corrective message a not-yet-exit gets (Stop hook block reason and the
    fworker resume line share it, so the worker reads the same contract both ways)."""
    tid = f"wq#{ticket_id}" if ticket_id else "this ticket"
    seen = ", ".join(decision.indicators) or "none"
    head = (f"EXIT GATE: the harness has not accepted your exit on {tid}. An exit is a "
            f"completion only when BOTH hold: >= {MIN_INDICATORS} harness-verified "
            f"completion indicators AND an explicit `EXIT_SIGNAL: true` line in your "
            f"final message. Indicators seen: {seen}.")
    if decision.reason == "signal_missing":
        body = ("Your final message carried no EXIT_SIGNAL line. If the ticket really is "
                "finished (landed, `wq done` run), reply with the two closing lines only: "
                "`EXIT_SIGNAL: true` then `TICKET_CLOSED`. If it is not finished, keep "
                "working; do not stop to wait.")
    elif decision.reason == "signal_false":
        body = ("You reported `EXIT_SIGNAL: false`, so the work is not finished: carry "
                "on. Stop only with `EXIT_SIGNAL: true` + `TICKET_CLOSED` once it has "
                "landed and `wq done` has run, or `wq block` + `EXIT_SIGNAL: false` + "
                "`BLOCKED` if it cannot be finished.")
    else:
        body = ("You declared `EXIT_SIGNAL: true` but the harness can verify fewer than "
                f"{MIN_INDICATORS} completion facts (ticket status done, landed commit, "
                "TICKET_CLOSED sentinel, clean result). Land the work via lumen-pr and "
                "run `wq done` before declaring exit, or block the ticket honestly.")
    return head + " " + body


# --------------------------------------------------------------------------
# loop facts read off the stream (shared by fworker and tests)
# --------------------------------------------------------------------------

_MUTATING_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
_QUESTION_RE = re.compile(
    r"(\?\s*$|\b(should i|do you want|would you like|which (one|option)|"
    r"please (confirm|advise|clarify)|waiting (for|on) (your|frank))\b)", re.IGNORECASE)


def _norm_error(text):
    t = (text or "")[-240:].lower()
    t = re.sub(r"0x[0-9a-f]+", "", t)
    t = re.sub(r"\d{2}:\d{2}:\d{2}(\.\d+)?", "", t)
    t = re.sub(r"\b\d+(\.\d+)?(s|ms)\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:160]


def _block_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def loop_stats(events):
    """Facts about one loop from its stream-json frames: how much the model said, whether
    a file-mutating tool succeeded, the last tool error (normalised), and whether the
    loop ended on a question. Tolerant of any frame shape — unknown frames are skipped."""
    out_chars, mutations, last_err, last_text = 0, 0, None, ""
    pending = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        content = (msg.get("content") if isinstance(msg, dict) else None) or []
        if not isinstance(content, list):
            continue
        if ev.get("type") == "assistant":
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    t = b.get("text") or ""
                    out_chars += len(t)
                    if t.strip():
                        last_text = t
                elif b.get("type") == "tool_use":
                    pending[b.get("id")] = b.get("name") or ""
                    out_chars += len(json.dumps(b.get("input") or {}))
        elif ev.get("type") == "user":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                tool = pending.pop(b.get("tool_use_id"), "")
                if b.get("is_error"):
                    last_err = _norm_error(_block_text(b)) or last_err
                elif tool in _MUTATING_TOOLS:
                    mutations += 1
    tail = last_text.strip()[-400:]
    return {"output_chars": out_chars, "mutations": mutations, "error_sig": last_err,
            "asking_questions": bool(tail and _QUESTION_RE.search(tail)),
            "final_text": last_text}


# --------------------------------------------------------------------------
# small persistence helpers
# --------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 2. circuit breaker
# --------------------------------------------------------------------------

class CircuitBreaker:
    """Per-ticket breaker persisted at <run_dir>/circuit-breaker.json.

    preflight() is asked before a loop is spawned; record_loop() is told what the loop
    did; reset() is the exit path (gate passed) and the manual reset."""

    def __init__(self, path, now=time.time):
        self.path = path
        self.now = now
        self.s = self._load()

    def _load(self):
        s = _read_json(self.path)
        s.setdefault("state", CLOSED)
        s.setdefault("opened_at", None)
        s.setdefault("reason", None)
        s.setdefault("no_progress", 0)
        s.setdefault("same_error", 0)
        s.setdefault("last_error_sig", None)
        s.setdefault("last_output_chars", None)
        s.setdefault("loops", 0)
        s.setdefault("history", [])
        return s

    def _save(self):
        try:
            _write_json(self.path, self.s)
        except OSError:
            pass

    def _transition(self, new, reason):
        old = self.s["state"]
        if new != old or reason != self.s.get("reason"):
            self.s["history"] = (self.s["history"] + [{
                "at": self.now(), "from": old, "to": new, "reason": reason,
                "loop": self.s["loops"]}])[-50:]
        self.s["state"] = new
        self.s["reason"] = reason
        self.s["opened_at"] = self.now() if new == OPEN else None

    @property
    def state(self):
        return self.s["state"]

    def cooldown_left(self):
        if self.s["state"] != OPEN or not self.s.get("opened_at"):
            return 0
        return max(0, CB_COOLDOWN_S - (self.now() - self.s["opened_at"]))

    def preflight(self):
        """(allow, reason). OPEN inside the cooldown refuses the loop; OPEN past it
        becomes HALF_OPEN and allows exactly the probe loop record_loop() then judges."""
        if self.s["state"] != OPEN:
            return True, None
        left = self.cooldown_left()
        if left > 0:
            return False, (f"circuit OPEN ({self.s.get('reason')}); half-open probe in "
                           f"{int(left // 60) + 1} min")
        self._transition(HALF_OPEN, f"cooldown elapsed after: {self.s.get('reason')}")
        # the probe starts from a clean slate, or its first loop would re-trip on the
        # counters that opened the circuit
        self.s["no_progress"] = 0
        self.s["same_error"] = 0
        self.s["last_output_chars"] = None
        self._save()
        return True, "half-open probe"

    def record_loop(self, progress, error_sig=None, output_chars=None,
                    asking_questions=False):
        """progress: True/False, or None when it could not be measured (never counted
        against the ticket). Returns the state after this loop."""
        s = self.s
        s["loops"] += 1
        prev_out = s.get("last_output_chars")
        was_half_open = s["state"] == HALF_OPEN

        if error_sig and error_sig == s.get("last_error_sig"):
            s["same_error"] += 1
        else:
            s["same_error"] = 1 if error_sig else 0
        s["last_error_sig"] = error_sig or None

        declined = (progress is False and not asking_questions
                    and isinstance(prev_out, int) and isinstance(output_chars, int)
                    and prev_out >= CB_DECLINE_MIN_BASE_CHARS
                    and output_chars < prev_out * (100 - CB_OUTPUT_DECLINE_PCT) / 100.0)
        if isinstance(output_chars, int):
            s["last_output_chars"] = output_chars

        if progress:
            s["no_progress"] = 0
        elif progress is False and not asking_questions:
            s["no_progress"] += 1
        # progress is None, or a question/park loop: counters held steady

        trip = None
        if s["same_error"] >= CB_SAME_ERROR_THRESHOLD:
            trip = f"same error {s['same_error']} loops: {(error_sig or '')[:80]!r}"
        elif s["no_progress"] >= CB_NO_PROGRESS_THRESHOLD:
            trip = f"no progress {s['no_progress']} loops"
        elif declined:
            trip = (f"output declined >{CB_OUTPUT_DECLINE_PCT}% "
                    f"({prev_out} -> {output_chars} chars) with no progress")
        elif was_half_open and progress is False and not asking_questions:
            trip = "half-open probe made no progress"

        if trip:
            self._transition(OPEN, trip)
        elif progress and s["state"] != CLOSED:
            self._transition(CLOSED, "progress")
        elif (s["state"] == CLOSED
              and s["no_progress"] >= max(1, CB_NO_PROGRESS_THRESHOLD - 1)):
            self._transition(HALF_OPEN, f"no progress {s['no_progress']} loops (monitoring)")
        self._save()
        return s["state"]

    def reset(self, reason="exit"):
        self._transition(CLOSED, reason)
        self.s["no_progress"] = 0
        self.s["same_error"] = 0
        self.s["last_error_sig"] = None
        self.s["last_output_chars"] = None
        self._save()


class CircuitLoopTracker:
    """Wraps action_gate.LoopTracker (unmodified) so the in-session same-error kill
    needs CB_SAME_ERROR_THRESHOLD occurrences instead of the tracker's single shared
    threshold of 3 — the no-progress triggers (same command class failing with no
    successful change between, same page re-read) stay at 3. A harness permission
    refusal keeps the tracker's own threshold: retrying a call the permission pack
    refuses cannot succeed, and wq#1107's PERMISSION_LOOP verdict depends on it."""

    _SAME_ERROR_RE = re.compile(r"^same error (\d+)x")

    def __init__(self, inner, same_error_n=None):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_same_error_n",
                           same_error_n or CB_SAME_ERROR_THRESHOLD)

    def observe(self, frame):
        inner = self._inner
        reason = inner.observe(frame)
        if reason and getattr(inner, "trip_kind", None) != "PERMISSION":
            m = self._SAME_ERROR_RE.match(reason)
            if m and int(m.group(1)) < self._same_error_n:
                inner.tripped = None
                inner.trip_kind = None
                # The tracker tests same-error first and returns, so the no-progress
                # trigger it skipped on this frame is re-evaluated here: the same
                # command class failing with no successful change between still trips
                # at the tracker's own threshold.
                for sig, count in inner.fail_counts.items():
                    if count >= inner.n:
                        kind = ("PERMISSION"
                                if inner.perm_counts.get(sig, 0) >= inner.n else None)
                        return inner._trip(
                            f"same command class failing {count}x with no "
                            f"intervening successful change: {sig}", kind)
                return None
        return reason

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)


# --------------------------------------------------------------------------
# 3. hourly call limit
# --------------------------------------------------------------------------

def take_call(runs_root, ticket_id, now=time.time, global_limit=None, ticket_limit=None):
    """Count one child invocation against this clock hour. Returns (ok, detail). Both
    counters zero when the YYYYMMDDHH stamp rolls. Refused calls are not counted."""
    glimit = MAX_CALLS_PER_HOUR if global_limit is None else global_limit
    tlimit = MAX_TICKET_CALLS_PER_HOUR if ticket_limit is None else ticket_limit
    path = os.path.join(runs_root, CALLS_FILENAME)
    hour = time.strftime("%Y%m%d%H", time.gmtime(now()))
    key = f"wq{ticket_id}"
    os.makedirs(runs_root, exist_ok=True)
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = _read_json(path)
        if d.get("hour") != hour:
            d = {"hour": hour, "global": 0, "tickets": {}}
        g, t = int(d.get("global", 0)), int(d.get("tickets", {}).get(key, 0))
        if glimit > 0 and g >= glimit:
            return False, f"fleet call limit {g}/{glimit} this hour ({hour}Z)"
        if tlimit > 0 and t >= tlimit:
            return False, f"ticket call limit {t}/{tlimit} this hour ({hour}Z)"
        d["global"] = g + 1
        d.setdefault("tickets", {})[key] = t + 1
        _write_json(path, d)
    return True, f"{g + 1}/{glimit} fleet, {t + 1}/{tlimit} ticket"


# --------------------------------------------------------------------------
# 4. session expiry
# --------------------------------------------------------------------------

def note_session(run_dir, session_id, now=time.time):
    """Remember when this ticket first saw a session id (its birth, for expiry)."""
    if not session_id:
        return
    path = os.path.join(run_dir, SESSIONS_FILENAME)
    d = _read_json(path)
    if session_id not in d:
        d[session_id] = now()
        if len(d) > 50:
            for sid in sorted(d, key=d.get)[:len(d) - 50]:
                d.pop(sid, None)
        try:
            _write_json(path, d)
        except OSError:
            pass


def session_expired(run_dir, session_id, now=time.time, expiry_s=None):
    """True only for a session this ticket is KNOWN to have started more than the
    expiry ago. An unknown session is not expired (nothing to measure against)."""
    limit = SESSION_EXPIRY_S if expiry_s is None else expiry_s
    born = _read_json(os.path.join(run_dir, SESSIONS_FILENAME)).get(session_id)
    return bool(limit > 0 and isinstance(born, (int, float)) and now() - born > limit)


# --------------------------------------------------------------------------
# 5. git backup per loop
# --------------------------------------------------------------------------

def _git(worktree, *args, env=None, timeout=60):
    return subprocess.run(["git", "-C", worktree, *args], capture_output=True,
                          text=True, timeout=timeout, env=env)


def worktree_fingerprint(worktree):
    """HEAD + porcelain status + tracked diff, hashed. None when git cannot answer —
    callers must read None as "progress unknown", never as "no progress"."""
    try:
        head = _git(worktree, "rev-parse", "HEAD")
        status = _git(worktree, "status", "--porcelain")
        diff = _git(worktree, "diff", "HEAD")
        if head.returncode or status.returncode or diff.returncode:
            return None
        h = hashlib.sha256()
        for part in (head.stdout, status.stdout, diff.stdout):
            h.update(part.encode("utf-8", "replace"))
            h.update(b"\0")
        return h.hexdigest()
    except (OSError, subprocess.SubprocessError):
        return None


def _small_untracked(worktree):
    """Untracked, non-ignored paths (bytes) that fit the per-file and total caps."""
    p = subprocess.run(["git", "-C", worktree, "ls-files", "-o", "--exclude-standard",
                        "-z"], capture_output=True, timeout=60)
    if p.returncode:
        return []
    keep, total = [], 0
    root = os.fsencode(worktree)
    for rel in p.stdout.split(b"\0"):
        if not rel:
            continue
        try:
            size = os.lstat(os.path.join(root, rel)).st_size
        except OSError:
            continue
        if size > BACKUP_MAX_UNTRACKED_FILE_BYTES:
            continue
        if total + size > BACKUP_MAX_UNTRACKED_TOTAL_BYTES:
            break
        total += size
        keep.append(rel)
    return keep


def git_backup(worktree, ticket_id, label):
    """Snapshot the worktree (tracked changes + small untracked files, .gitignore
    honoured, large untracked files left out — see BACKUP_MAX_UNTRACKED_*) as a commit
    under refs/foreman-backup/wq<id>/<label>, through a throwaway index so neither the
    working tree nor the real index is touched. Returns the ref, or None (best-effort:
    a failed backup never stops a loop)."""
    try:
        if _git(worktree, "rev-parse", "--is-inside-work-tree").returncode:
            return None
        gitdir = _git(worktree, "rev-parse", "--absolute-git-dir").stdout.strip()
        head = _git(worktree, "rev-parse", "--verify", "-q", "HEAD").stdout.strip()
        idx = os.path.join(gitdir, f"foreman-backup-index.{os.getpid()}")
        env = dict(os.environ, GIT_INDEX_FILE=idx,
                   GIT_AUTHOR_NAME="foreman", GIT_AUTHOR_EMAIL="foreman@lumen",
                   GIT_COMMITTER_NAME="foreman", GIT_COMMITTER_EMAIL="foreman@lumen")
        try:
            if head and _git(worktree, "read-tree", head, env=env).returncode:
                return None
            if _git(worktree, "add", "-u", env=env, timeout=120).returncode:
                return None
            extra = _small_untracked(worktree)
            if extra:
                added = subprocess.run(
                    ["git", "-C", worktree, "add", "--pathspec-from-file=-",
                     "--pathspec-file-nul"], input=b"\0".join(extra),
                    capture_output=True, env=env, timeout=120)
                if added.returncode:
                    return None
            tree = _git(worktree, "write-tree", env=env).stdout.strip()
            if not tree:
                return None
            cargs = ["commit-tree", tree, "-m", f"foreman loop backup wq#{ticket_id} {label}"]
            if head:
                cargs += ["-p", head]
            commit = _git(worktree, *cargs, env=env).stdout.strip()
        finally:
            try:
                os.unlink(idx)
            except OSError:
                pass
        if not commit:
            return None
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", str(label))
        ref = f"{BACKUP_REF_ROOT}/wq{ticket_id}/{int(time.time())}-{safe}"
        if _git(worktree, "update-ref", ref, commit).returncode:
            return None
        for old in list_backups(worktree, ticket_id)[:-BACKUP_KEEP]:
            _git(worktree, "update-ref", "-d", old)
        return ref
    except (OSError, subprocess.SubprocessError):
        return None


def list_backups(worktree, ticket_id):
    """Backup refs for a ticket, oldest first."""
    try:
        p = _git(worktree, "for-each-ref", "--sort=creatordate",
                 "--format=%(refname)", f"{BACKUP_REF_ROOT}/wq{ticket_id}/")
        return [r for r in p.stdout.split() if r] if p.returncode == 0 else []
    except (OSError, subprocess.SubprocessError):
        return []


def rollback(worktree, ref):
    """Restore tracked + snapshot files from a backup ref into the worktree. Files
    created after the snapshot are left in place (nothing is deleted)."""
    p = _git(worktree, "restore", "--source", ref, "--worktree", "--", ".")
    return p.returncode == 0, (p.stderr or "").strip()


def _cli(argv):
    if len(argv) >= 2 and argv[0] == "status":
        cb = CircuitBreaker(os.path.join(argv[1], BREAKER_FILENAME))
        print(json.dumps({k: cb.s[k] for k in ("state", "reason", "no_progress",
                                                 "same_error", "loops")}, indent=1))
        return 0
    if len(argv) >= 2 and argv[0] == "reset":
        CircuitBreaker(os.path.join(argv[1], BREAKER_FILENAME)).reset("manual reset")
        print("CLOSED")
        return 0
    if len(argv) >= 3 and argv[0] == "backups":
        print("\n".join(list_backups(argv[1], argv[2])))
        return 0
    if len(argv) >= 3 and argv[0] == "rollback":
        ok, err = rollback(argv[1], argv[2])
        print("restored" if ok else f"FAILED: {err}")
        return 0 if ok else 1
    print("usage: exit_gate.py status <run_dir> | reset <run_dir> | "
          "backups <worktree> <ticket> | rollback <worktree> <ref>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
