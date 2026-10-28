"""hold_gate.py — PreToolUse hold hook for capability-denied reads (Pilot 1).

docs/agent-authority-research/DESIGN.md §2.2 (holdable classes), §2.3 steps 1+7
(request + consume), §2.6 (fingerprint), §2.7 F1-F5 (fail-closed table). B1-T1:
the hook exists and is testable standalone; it is NOT wired into
worker-settings.json yet (B1-T5 does that, together with fworker's
tool_deferred handling and the FOREMAN_ADD_DIRS export).

Decision surface (stdout JSON hookSpecificOutput.permissionDecision, exit 0):
  defer — the call is holdable (H1 path-read / H2 repo-read), every lower layer
          would allow it, and no approval matches its fingerprint: a pending
          grant_requests row is written and the call is held (§2.3 step 1).
  allow — an approved, unexpired, unconsumed approval matches the fingerprint;
          it is consumed atomically (§2.3 step 7, F5: exactly one winner).
  deny  — a lower layer (guard.sh / action_gate) denies the call (§2.2 order
          independence, F3), or the matching request is denied / expired /
          already consumed (F4/F5).
  no decision (exit 0, no output) — everything else: not a holdable class,
          writes of any kind, credential/deny-listed paths
          (capability_lint._deny_reason), paths inside add_dirs, mcp__ tools,
          AMBER Bash (the real action_gate hook owns the snapshot), or a
          missing ticket/tool_use_id. Today's flow stays in charge.

Fail-closed story (§2.7 F1): any internal error, unreadable db, or guard
timeout ends in exit 1 (non-blocking hook error) or an explicit deny — never a
defer. A crashed hold_gate leaves the allowlist flow in charge, so the call is
denied as today (CAPABILITY_DENIED).

Lower-layer pre-check (§2.2 "Order independence"): hook-decision precedence
across multiple PreToolUse hooks is undocumented, so hold_gate never relies on
it. Before any defer it re-runs guard.sh on the same stdin payload (subprocess
— guard is bash) and action_gate.classify_bash in-process, and defers only if
both would allow. The guard pre-run gets an isolated FOREMAN_EVENTS_DIR so its
wq#730 R5 read-cache and event log never pollute the session's own guard state
(a cache entry written by the pre-run would otherwise make guard.sh suppress
the approved re-read on resume — B1-T5 must also handle the session guard run
that precedes the defer itself).

Env interface (B1-T5 wires the first two at spawn):
  FOREMAN_TICKET_ID   ticket id bound into the fingerprint; missing → no decision
  FOREMAN_ADD_DIRS    colon-separated --add-dir roots granted to this worker
  FOREMAN_WORKTREE_ROOT, cwd — always treated as in-scope
  FOREMAN_HOLD_DB     sqlite holding grant_requests (default: the wq queue.db,
                      DESIGN.md §2.6 "new table in the wq sqlite")
  FOREMAN_GRANT_FILE  ticket grant json; a repo grant covering the target repo
                      makes an H2 read non-holdable (the broker owns access)
  FOREMAN_HOLD_GATE   "0"/"off" → hook is inert (no decision, ever)

B1-T2 stub: the grant_requests table and the approve/deny/expire lifecycle
belong to B1-T2 (`wq grant`). Until it lands, this module creates the table
with the §2.6 schema if missing and consumes rows with the same atomic UPDATE
B1-T2 will use, so the interface (fingerprint in, one-shot consume out) is
frozen here and B1-T2 only replaces the writer side.
"""

import fnmatch
import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)

import action_gate  # noqa: E402
import capability_lint  # noqa: E402

GUARD_SH = os.path.join(HARNESS_DIR, "guard.sh")
GUARD_TIMEOUT_S = 15
DEFAULT_HOLD_DB = "/home/lumen/agents/work-queue/queue.db"
APPROVAL_TTL_H = 24  # §2.3 step 4

# §2.2 H1: the read tools and the allow-listed read-only Bash verbs. Closed
# lists — growing either is a design change, not a tweak.
READ_TOOL_PATH_KEYS = {"Read": "file_path", "Grep": "path", "Glob": "path"}
READ_VERBS = frozenset({"ls", "cat", "grep", "stat", "head", "tail"})

# A compound/redirected command has more than one thing to grant; never holdable.
_SHELL_META = ("|", ";", "&", ">", "<", "`", "$(")

# §2.2 "guard credential deny": guard.sh's Read/Grep credential case-block
# (guard.sh:675) mirrored for classification, so a credential path is never
# even classified holdable (on top of the guard pre-run, which would deny it).
_GUARD_CRED_GLOBS = (
    "*.env", "*.credentials.json", "*claude_credentials.md",
    "*claude_credentials.md.*", "/home/lumen/minerva/context/sections/*credentials*",
    "/home/lumen/minerva/context/sections.json",
    "/home/lumen/minerva/context/sections.json.*",
    "/home/lumen/agent/.env*", "*/.claude/*", "/home/lumen/.claude/*",
    "/home/lumen/.secrets", "/home/lumen/.secrets/*",
    "/var/lib/token-engine", "/var/lib/token-engine/*",
    "/home/lumen/logs/supergateway", "/home/lumen/logs/supergateway/*",
)

GRANT_REQUESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS grant_requests (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id     TEXT NOT NULL,
  session_id    TEXT NOT NULL,
  tool_use_id   TEXT NOT NULL,
  class         TEXT NOT NULL,
  target        TEXT NOT NULL,
  fingerprint   TEXT NOT NULL UNIQUE,
  state         TEXT NOT NULL DEFAULT 'pending',
  requested_at  TEXT NOT NULL,
  decided_by    TEXT,
  decided_at    TEXT,
  reason        TEXT,
  expires_at    TEXT,
  consumed_at   TEXT,
  consumed_lease TEXT
)
"""


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_event(event, **fields):
    """Best-effort JSONL line, same contract as guard.sh's log_event: a logging
    failure must never change a decision."""
    try:
        d = os.environ.get("FOREMAN_EVENTS_DIR", "/home/lumen/foreman/events")
        os.makedirs(d, exist_ok=True)
        rec = {"ts": _iso(_now()), "worker": os.environ.get("FOREMAN_WORKER", "unknown"),
               "event": event}
        rec.update(fields)
        with open(os.path.join(d, "hold_gate.jsonl"), "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# classification — §2.2 closed list
# ---------------------------------------------------------------------------

def add_dirs(cwd):
    dirs = [d for d in os.environ.get("FOREMAN_ADD_DIRS", "").split(":") if d]
    wt = os.environ.get("FOREMAN_WORKTREE_ROOT", "")
    if wt:
        dirs.append(wt)
    if cwd:
        dirs.append(cwd)
    return [os.path.realpath(d) for d in dirs]


def _inside(path, roots):
    for r in roots:
        if path == r or path.startswith(r + os.sep):
            return True
    return False


def _guard_cred_hit(path):
    for g in _GUARD_CRED_GLOBS:
        if fnmatch.fnmatch(path, g):
            return g
    return None


def _managed_repo_root(target_realpath):
    """realpath of the managed repo root containing target, or None. An import
    failure means H2 detection is unavailable; the read then classifies as H1
    (a plain path hold), never as an error."""
    try:
        import repo_sweep
        roots = {os.path.realpath(r) for r in repo_sweep.managed_repos()}
    except Exception:
        return None
    for r in sorted(roots, key=len, reverse=True):
        if target_realpath == r or target_realpath.startswith(r + os.sep):
            return r
    return None


def _repo_granted(repo_root):
    """B1-T3 stub: True when the ticket's grant file carries a repo grant whose
    value names this repo root or its managed_repos key. The broker (repo-read)
    does the authoritative sha-pinned check; hold_gate only decides hold-vs-not."""
    path = os.environ.get("FOREMAN_GRANT_FILE")
    if not path:
        return False
    try:
        with open(path) as f:
            grant = json.load(f)
        for g in grant.get("grants", []):
            if g.get("kind") != "repo":
                continue
            val = str(g.get("repo", ""))
            key = val.split("@", 1)[0]
            if key and (key == repo_root or os.path.realpath(key) == repo_root):
                return True
    except Exception:
        return False
    return False


def _bash_target(cmd, dirs):
    """The single out-of-scope absolute path of an allow-listed read-only Bash
    verb, or None. Compound commands, non-read verbs, zero or multiple outside
    paths: not holdable (a hold materializes exactly one grant)."""
    if any(m in cmd for m in _SHELL_META):
        return None
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    if not tokens or os.path.basename(tokens[0]) not in READ_VERBS:
        return None
    outside = []
    for t in tokens[1:]:
        if t.startswith("-") or not t.startswith("/"):
            continue
        rp = os.path.realpath(t)
        if not _inside(rp, dirs):
            outside.append(rp)
    uniq = sorted(set(outside))
    if len(uniq) != 1:
        return None
    return uniq[0]


def classify(tool_name, tool_input, cwd):
    """(class, cited_path, target_realpath) for a holdable call, else None.
    class is 'H1' (path-read) or 'H2' (repo-read). Everything on the §2.2
    'not holdable, ever' list — writes, credential/deny-listed paths, unlisted
    Bash verbs, mcp__ tools — returns None here."""
    dirs = add_dirs(cwd)
    if tool_name in READ_TOOL_PATH_KEYS:
        cited = tool_input.get(READ_TOOL_PATH_KEYS[tool_name]) or ""
        if not cited:
            return None
        if not os.path.isabs(cited):
            cited = os.path.join(cwd or "/", cited)
        target = os.path.realpath(cited)
    elif tool_name == "Bash":
        cmd = tool_input.get("command") or ""
        target = _bash_target(cmd, dirs)
        if target is None:
            return None
        cited = target
    else:
        return None

    if _inside(target, dirs):
        return None
    # §2.2: rejected by capability_lint._deny_reason or the guard credential
    # deny → not holdable, ever (checked on the path as cited AND as resolved,
    # so a symlink can't launder either side).
    for p in (cited, target):
        if capability_lint._deny_reason(p) or _guard_cred_hit(p):
            return None

    repo_root = _managed_repo_root(target)
    if repo_root is not None:
        if _repo_granted(repo_root):
            return None  # covered by the ticket's grant: broker's business
        return ("H2", cited, target)
    return ("H1", cited, target)


# ---------------------------------------------------------------------------
# lower-layer pre-check — §2.2 order independence, §2.7 F3
# ---------------------------------------------------------------------------

def guard_denies(raw_payload):
    """Re-run guard.sh on the exact stdin payload. Returns a deny reason string
    or None. Any non-zero exit (block, internal error, timeout) is a deny:
    hold_gate never defers a call guard did not cleanly allow."""
    env = dict(os.environ)
    # Isolate the pre-run's side effects (event log + wq#730 R5 read-cache).
    env["FOREMAN_EVENTS_DIR"] = tempfile.mkdtemp(prefix="hold_gate_guard_")
    try:
        p = subprocess.run([GUARD_SH], input=raw_payload, capture_output=True,
                           text=True, env=env, timeout=GUARD_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"guard.sh pre-run failed ({e.__class__.__name__})"
    if p.returncode == 0:
        return None
    tail = (p.stderr or "").strip().splitlines()
    return tail[-1] if tail else f"guard.sh exit {p.returncode}"


def action_gate_denies(tool_name, tool_input, cwd):
    """In-process action_gate verdict. 'deny' for RED, 'amber' for AMBER (the
    real hook owns the snapshot, so an AMBER read is not held), None for GREEN.
    Only Bash can be anything but GREEN here: action_gate treats Read/Grep/Glob
    as GREEN by definition (action_gate.hook_main)."""
    if tool_name != "Bash":
        return None
    rules = action_gate.load_compiled_rules()
    verdict, reason, _snap, _hw = action_gate.classify_bash(
        tool_input.get("command") or "", rules, cwd)
    if verdict == "RED":
        return ("deny", reason)
    if verdict == "AMBER":
        return ("amber", reason)
    return None


# ---------------------------------------------------------------------------
# fingerprint — §2.6
# ---------------------------------------------------------------------------

def canonical_input(tool_name, tool_input):
    """tool_input with keys sorted (canonical json does that), paths realpath'd
    and whitespace normalized in Bash commands."""
    ti = dict(tool_input)
    if tool_name == "Bash" and ti.get("command"):
        ti["command"] = " ".join(str(ti["command"]).split())
    for key in ("file_path", "path"):
        if ti.get(key):
            ti[key] = os.path.realpath(str(ti[key]))
    return ti


def fingerprint(ticket, session_id, tool_use_id, tool_name, input_canonical,
                cls, target_realpath):
    payload = {"ticket": str(ticket), "session_id": session_id,
               "tool_use_id": tool_use_id, "tool_name": tool_name,
               "input_canonical": input_canonical, "class": cls,
               "target_realpath": target_realpath}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------------------
# approvals — §2.3 steps 1+7, §2.7 F4/F5 (B1-T2 stub schema)
# ---------------------------------------------------------------------------

def _db():
    path = os.environ.get("FOREMAN_HOLD_DB", DEFAULT_HOLD_DB)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute(GRANT_REQUESTS_SCHEMA)
    return conn


def resolve_approval(conn, fp, *, ticket, session_id, tool_use_id, cls, target):
    """(decision, reason). defer writes/keeps the pending row; allow consumes
    the approval atomically (one UPDATE, one winner — F5); denied/expired/
    consumed states deny (F4 + ticket spec: a consumed approval is a deny,
    never a re-defer)."""
    now = _now()
    row = conn.execute(
        "SELECT id, state, expires_at, consumed_at FROM grant_requests "
        "WHERE fingerprint = ?", (fp,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO grant_requests (ticket_id, session_id, tool_use_id, class,"
            " target, fingerprint, state, requested_at, expires_at)"
            " VALUES (?,?,?,?,?,?, 'pending', ?, ?)",
            (str(ticket), session_id, tool_use_id, cls, target, fp,
             _iso(now), _iso(now + timedelta(hours=APPROVAL_TTL_H))))
        conn.commit()
        return ("defer", f"held for approval (grant request {cur.lastrowid}, "
                         f"class {cls}, target {target}). If this call was issued "
                         "in parallel with other tool calls, re-issue it alone.")
    req_id, state, expires_at, consumed_at = row
    if state == "pending":
        return ("defer", f"still held for approval (grant request {req_id}). If this "
                         "call was issued in parallel with other tool calls, "
                         "re-issue it alone.")
    if state == "approved":
        if expires_at and _iso(now) > expires_at:
            conn.execute("UPDATE grant_requests SET state='expired' "
                         "WHERE id = ? AND state = 'approved'", (req_id,))
            conn.commit()
            return ("deny", f"approval for grant request {req_id} expired at {expires_at}")
        cur = conn.execute(
            "UPDATE grant_requests SET state='consumed', consumed_at=?, consumed_lease=?"
            " WHERE fingerprint = ? AND state = 'approved' AND consumed_at IS NULL",
            (_iso(now), os.environ.get("FOREMAN_WORKER", ""), fp))
        conn.commit()
        if cur.rowcount == 1:
            return ("allow", f"approved grant request {req_id} consumed (one-shot)")
        return ("deny", f"approval for grant request {req_id} was already consumed")
    if state == "consumed":
        return ("deny", f"approval for grant request {req_id} was already consumed "
                        f"at {consumed_at}")
    if state == "denied":
        return ("deny", f"grant request {req_id} was denied")
    if state == "expired":
        return ("deny", f"grant request {req_id} expired")
    return ("deny", f"grant request {req_id} in unknown state {state!r}")


# ---------------------------------------------------------------------------
# hook entrypoint
# ---------------------------------------------------------------------------

def _emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": f"hold_gate: {reason}"}}))
    return 0


def hook_main():
    raw = sys.stdin.read()
    try:
        envelope = json.loads(raw)
    except (ValueError, TypeError):
        print("hold_gate: unparseable hook JSON — no decision", file=sys.stderr)
        return 1
    if os.environ.get("FOREMAN_HOLD_GATE", "").lower() in ("0", "off"):
        return 0
    tool_name = envelope.get("tool_name") or ""
    tool_input = envelope.get("tool_input") or {}
    session_id = envelope.get("session_id") or ""
    tool_use_id = envelope.get("tool_use_id") or ""
    cwd = envelope.get("cwd") or os.getcwd()
    ticket = os.environ.get("FOREMAN_TICKET_ID", "")

    hit = classify(tool_name, tool_input, cwd)
    if hit is None:
        return 0
    cls, cited, target = hit
    # §2.6: the fingerprint binds the approval to the one pending call; without
    # a ticket identity or tool_use_id that binding is meaningless — fall
    # through to today's flow rather than hold an unbindable request.
    if not ticket or not tool_use_id or not session_id:
        _log_event("hold_gate_unbindable", tool=tool_name, cls=cls, target=target,
                   missing=[k for k, v in (("ticket", ticket),
                                           ("tool_use_id", tool_use_id),
                                           ("session_id", session_id)) if not v])
        return 0

    # F3: pre-run the lower layers; any deny is a deny, never a hold.
    why = guard_denies(raw)
    if why is not None:
        _log_event("hold_gate_lower_deny", layer="guard", tool=tool_name,
                   cls=cls, target=target, reason=why[:300])
        return _emit("deny", f"lower layer (guard.sh) denies this call: {why}")
    ag = action_gate_denies(tool_name, tool_input, cwd)
    if ag is not None:
        kind, reason = ag
        if kind == "deny":
            _log_event("hold_gate_lower_deny", layer="action_gate", tool=tool_name,
                       cls=cls, target=target, reason=reason[:300])
            return _emit("deny", f"lower layer (action_gate) denies this call: {reason}")
        return 0  # AMBER: the real action_gate hook owns the snapshot path

    fp = fingerprint(ticket, session_id, tool_use_id, tool_name,
                     canonical_input(tool_name, tool_input), cls, target)
    conn = _db()
    try:
        decision, reason = resolve_approval(
            conn, fp, ticket=ticket, session_id=session_id,
            tool_use_id=tool_use_id, cls=cls, target=target)
    finally:
        conn.close()
    _log_event(f"hold_gate_{decision}", tool=tool_name, cls=cls, target=target,
               fingerprint=fp, reason=reason[:300])
    return _emit(decision, reason)


def main():
    # F1: ANY internal error must end without a defer on stdout. Exit 1 is the
    # non-blocking hook error; the allowlist flow stays in charge and the call
    # is denied as today.
    try:
        return hook_main()
    except Exception as e:
        print(f"hold_gate: internal error — no decision ({e})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
