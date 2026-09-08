"""fire_gate.py — wq#717 (AUTO-5) safety core for the fleet-fires manual-approval
workflow. Pure file-IO helpers only, no subprocess and no claude spawn, so the
safety-critical checks (approval integrity, plan-drift, timeout) are unit-testable
without a live child. fworker.py's fire-mode orchestration (run_fire_ticket) is the
only caller — see harness/FIRE_MODE.md for the full workflow this implements.

Guardrail note (ticket MUST "never approve itself"): the bearer token lives at
FIRE_TOKEN_FILE, under /home/lumen/.secrets — a path guard.sh already denies to every
worker's Bash/Read/Edit/Write tool calls (and, as of this ticket, Grep/Glob too — see
guard.sh's Grep|Glob case). A fire-mode child (the sandboxed `claude -p` process this
gate exists to constrain) structurally cannot read the secret it would need to forge an
approval. Only fworker's own supervisor process — outside the guarded child's tool
sandbox, same trust boundary as OAUTH_TOKEN_FILE (wq#616) — ever opens FIRE_TOKEN_FILE.
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
PLANS_DIR = os.environ.get("FOREMAN_PLANS_DIR", os.path.join(FOREMAN_ROOT, "plans"))
APPROVALS_DIR = os.environ.get("FOREMAN_APPROVALS_DIR",
                                os.path.join(FOREMAN_ROOT, "approvals"))
FIRE_TOKEN_FILE = os.environ.get("FOREMAN_FIRE_TOKEN_FILE",
                                  "/home/lumen/.secrets/foreman-fire-approval.token")
DEFAULT_TIMEOUT_HOURS = 24

# wq#1234: re-verify + max-runtime for the fleet-fires manual-approval workflow. wq#1154/
# #1160/#1161 all ran 13-22h after the underlying probe condition had already cleared,
# because nothing ever re-ran the originating probe or bounded total wall-clock.
AQ_DB = os.environ.get("FOREMAN_FIRE_AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
# wq#1261 (#13 item 2): fire-lane default bumped 45min -> 2h, matching
# ISSUE-SOLUTIONS-fable.md §4's explicit "max_runtime_s ... fire-lane default 7200".
# Still overridable per-run via FOREMAN_FIRE_MAX_RUNTIME_S (fworker.py, router.py both
# read the same env var so the internal watchdog and the external `timeout` wrap agree).
DEFAULT_MAX_RUNTIME_S = int(os.environ.get("FOREMAN_FIRE_MAX_RUNTIME_S", 2 * 3600))

# Matches aq_escalate.seed_ticket's detail template exactly (bin/aq_escalate.py):
#   "Probe: {probe}\naq row: #{row['id']} (source=..., priority=...)\n"
PROBE_RE = re.compile(r"^Probe:\s*(\S+)", re.MULTILINE)
AQ_ROW_RE = re.compile(r"aq row:\s*#(\d+)", re.MULTILINE)

REQUIRED_APPROVAL_FIELDS = ("ticket", "plan_hash", "token")


def plan_path(ticket_id):
    return os.path.join(PLANS_DIR, f"wq{ticket_id}.md")


def approval_path(ticket_id):
    return os.path.join(APPROVALS_DIR, f"wq{ticket_id}.approved")


def drift_path(ticket_id):
    """Written by the apply-phase child if its mandatory re-verify step finds the
    live state no longer matches the approved plan's "Current State" section — see
    FIRE_MODE.md's re-verify contract. Presence means "treat as never applied"."""
    return os.path.join(PLANS_DIR, f"wq{ticket_id}.drift")


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_plan(ticket_id):
    path = plan_path(ticket_id)
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return f.read()


def write_plan(ticket_id, text):
    os.makedirs(PLANS_DIR, exist_ok=True)
    with open(plan_path(ticket_id), "w") as f:
        f.write(text)


def clear_fire_state(ticket_id):
    """Drop the plan/approval/drift files for a ticket — used when a timed-out
    diagnosis is released so the next attempt starts a clean diagnosis rather than
    inheriting a stale plan an old, expired approval might still (wrongly) match."""
    for path in (plan_path(ticket_id), approval_path(ticket_id), drift_path(ticket_id)):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def read_token():
    if not os.path.isfile(FIRE_TOKEN_FILE):
        return None
    with open(FIRE_TOKEN_FILE) as f:
        return f.read().strip()


def load_approval(ticket_id):
    """Returns (approval_dict_or_None, error_code_or_None). error_code is one of
    'missing' | 'malformed_json' | 'missing_fields' on failure, else None."""
    path = approval_path(ticket_id)
    if not os.path.isfile(path):
        return None, "missing"
    try:
        with open(path) as f:
            raw = f.read()
        approval = json.loads(raw)
    except (OSError, ValueError):
        return None, "malformed_json"
    if not isinstance(approval, dict) or any(k not in approval for k in REQUIRED_APPROVAL_FIELDS):
        return None, "missing_fields"
    return approval, None


def validate_approval(ticket_id, plan_text):
    """Full integrity check of the on-disk approval file against the CURRENT plan and
    the secret token. Returns (ok, code, detail). code == "ok" iff ok is True; every
    other code is a documented failure mode (FIRE_MODE.md's failure-modes table):

      missing             no approval file yet — normal hold state, not an error
      malformed_json      approval file exists but isn't valid JSON
      missing_fields      JSON parsed but lacks ticket/plan_hash/token
      ticket_mismatch     approval names a different ticket (no copy-paste reuse
                          of one ticket's approval onto another)
      token_file_missing  FIRE_TOKEN_FILE is absent (mis-provisioned host — fail
                          closed, never treat "can't check" as "approved")
      bad_token           token doesn't match FIRE_TOKEN_FILE (never a self-approval:
                          the worker/child cannot read that path — see module docstring)
      plan_missing        no plan on disk to compare the approval's plan_hash against
      plan_drift          plan_hash doesn't match the plan currently on disk — the
                          plan changed (or was regenerated) since Frank approved it;
                          MUST NOT apply, per the ticket's re-verify guardrail
    """
    approval, err = load_approval(ticket_id)
    if err:
        return False, err, approval_path(ticket_id)
    if str(approval.get("ticket")) != str(ticket_id):
        return False, "ticket_mismatch", f"approval is for ticket {approval.get('ticket')!r}"
    secret = read_token()
    if secret is None:
        return False, "token_file_missing", FIRE_TOKEN_FILE
    if approval.get("token") != secret:
        return False, "bad_token", "bearer token does not match FIRE_TOKEN_FILE"
    if plan_text is None:
        return False, "plan_missing", "no plan on disk to compare against"
    if approval.get("plan_hash") != sha256_text(plan_text):
        return False, "plan_drift", "approval's plan_hash no longer matches plans/wq<ID>.md"
    return True, "ok", None


def diagnosis_timed_out(ticket_id, timeout_hours=DEFAULT_TIMEOUT_HOURS, now=None):
    """True once a plan has existed (awaiting a valid approval) longer than
    timeout_hours. The plan file's own mtime is the hold clock's start — no separate
    ledger to fall out of sync with it, and a rewritten plan (re-diagnosis) naturally
    resets the clock."""
    path = plan_path(ticket_id)
    if not os.path.isfile(path):
        return False
    now = now if now is not None else time.time()
    return (now - os.path.getmtime(path)) > timeout_hours * 3600


def next_action(ticket_id, timeout_hours=DEFAULT_TIMEOUT_HOURS, now=None):
    """Pure decision of what the fire-mode supervisor should do right now. Returns
    (action, info) where action is one of:

      "diagnose"        no plan on disk yet — spawn the read-only diagnosis child
      "apply"           plan + a currently-valid approval exist — spawn the
                        production-change child; info["plan_text"] is the approved plan
      "hold"            plan exists, no valid approval yet, within timeout — wait;
                        info["code"]/["detail"] explain why (from validate_approval)
      "timeout_release" plan exists, no valid approval, timeout exceeded — release
                        the ticket; info["code"]/["detail"] carry the last-seen reason
    """
    plan_text = read_plan(ticket_id)
    if plan_text is None:
        return "diagnose", {}
    ok, code, detail = validate_approval(ticket_id, plan_text)
    if ok:
        return "apply", {"plan_text": plan_text}
    if diagnosis_timed_out(ticket_id, timeout_hours, now):
        return "timeout_release", {"code": code, "detail": detail}
    return "hold", {"code": code, "detail": detail}


def parse_probe_ref(detail):
    """Extract (probe_name, aq_id) from a fleet-fires ticket's detail text, in the exact
    shape aq_escalate.seed_ticket writes it. Returns (None, None) for a fire-response
    ticket that wasn't probe-originated (no "Probe:"/"aq row: #<id>" lines) — such a
    ticket has no aq row to re-verify against."""
    if not detail:
        return None, None
    probe_m = PROBE_RE.search(detail)
    aq_m = AQ_ROW_RE.search(detail)
    probe = probe_m.group(1) if probe_m else None
    aq_id = int(aq_m.group(1)) if aq_m else None
    return probe, aq_id


def probe_condition_cleared(detail, aq_db=None):
    """Re-verify step (wq#1234): True iff the aq alert-queue row that originated this
    fire ticket is no longer 'open'. The independent sentinel probe cycle is the only
    thing that ever clears a row (edge-clear on PASS, see aq_escalate.seed_ticket's
    Acceptance text and sentinel_probes.py's EDGE-CLEAR path) — this never re-runs the
    probe itself, it just asks whether sentinel already has.

    Fails closed (returns False, i.e. "still on fire") whenever the answer can't be
    determined: no parseable aq row reference, no aq db file, unreadable db, or no
    matching row — "can't check" must never be mistaken for "cleared"."""
    _, aq_id = parse_probe_ref(detail)
    if aq_id is None:
        return False
    path = aq_db or AQ_DB
    if not os.path.isfile(path):
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("SELECT status FROM alerts WHERE id=?", (aq_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    return row[0] != "open"


# --- wq#1261 (#13 item 1): fire_probe schema — a machine-checkable re-verify a worker
# can run directly, instead of only being able to ask whether aq_escalate's alerts.db row
# has cleared. This is what lets a synthetic/drill fire ticket (no aq row at all, see
# tests/test_fire_reverify.py) carry its own re-verify condition, and it's what the
# dispatcher's no-probe refusal (wq#13 item 1's "a fire you can't re-verify is a FRANK
# page, not a worker") checks for before ever spawning fworker on a fleet-fires ticket.
FIRE_PROBE_RE = re.compile(r"^fire_probe:\s*(\{.*\})\s*$", re.MULTILINE)
DEFAULT_PROBE_TIMEOUT_S = 60


def parse_fire_probe(detail):
    """Extract the {"cmd": ..., "clear_when_exit": ...} schema from a fire ticket's
    detail text — one line, `fire_probe: {...}` (JSON). Returns the dict (with only
    those two keys, exactly as given) or None if the ticket carries no such line, or
    the line is present but malformed (not valid JSON / not an object / cmd not a
    non-empty string / clear_when_exit not an int) — malformed is treated the same as
    absent everywhere this is checked: a probe you can't trust is not a probe."""
    if not detail:
        return None
    m = FIRE_PROBE_RE.search(detail)
    if not m:
        return None
    try:
        probe = json.loads(m.group(1))
    except (ValueError, TypeError):
        return None
    if not isinstance(probe, dict):
        return None
    cmd = probe.get("cmd")
    clear_when_exit = probe.get("clear_when_exit")
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if not isinstance(clear_when_exit, int) or isinstance(clear_when_exit, bool):
        return None
    return {"cmd": cmd, "clear_when_exit": clear_when_exit}


def run_fire_probe(probe, timeout_s=DEFAULT_PROBE_TIMEOUT_S, now=None):
    """Runs probe['cmd'] through the shell and reports whether it cleared. Returns
    (cleared, evidence) — evidence is exactly what a fire ticket's auto-close attaches
    as its evidence.json (ISSUE-SOLUTIONS-fable.md §4 item 1: "the worker auto-closes
    with the probe output as its evidence").

    Fails closed (cleared=False) on a timeout or a spawn failure — same fail-closed
    contract as probe_condition_cleared: "can't check" must never read as "cleared"."""
    now = now if now is not None else time.time()
    evidence = {"cmd": probe["cmd"], "clear_when_exit": probe["clear_when_exit"],
                "ran_at": now}
    try:
        result = subprocess.run(probe["cmd"], shell=True, capture_output=True,
                                 text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        evidence.update(exit_code=None, stdout="", stderr=f"probe timed out after {timeout_s}s",
                         cleared=False)
        return False, evidence
    except OSError as e:
        evidence.update(exit_code=None, stdout="", stderr=str(e), cleared=False)
        return False, evidence
    cleared = result.returncode == probe["clear_when_exit"]
    evidence.update(exit_code=result.returncode, stdout=result.stdout[-4000:],
                     stderr=result.stderr[-2000:], cleared=cleared)
    return cleared, evidence


def probe_cleared(detail, aq_db=None, timeout_s=DEFAULT_PROBE_TIMEOUT_S, now=None):
    """The re-verify step run_fire_ticket() actually calls each iteration. Prefers the
    explicit fire_probe cmd schema (wq#1261); falls back to the aq alert-queue row check
    (wq#1234) for tickets seeded before this schema existed, so old in-flight fire
    tickets keep working unchanged. Returns (cleared, evidence_or_None) — evidence is
    only ever populated by the cmd-based probe (literal command output); the aq-row
    fallback has nothing to attach, so it reports (bool, None)."""
    probe = parse_fire_probe(detail)
    if probe is not None:
        return run_fire_probe(probe, timeout_s=timeout_s, now=now)
    cleared = probe_condition_cleared(detail, aq_db=aq_db)
    if not cleared:
        return False, None
    # wq#1487: the aq-row fallback used to return (True, None). run_fire_ticket()
    # then called `wq done` with notes that named NO artifact, the wq#759 evidence
    # gate refused it, the refusal became an unhandled exception, the claim was
    # released, and the dispatcher re-fired the ticket forever (wq#1424: 312
    # attempts, a `foreman CRASH` page every cycle — the "lumen crashed" cry-wolf).
    # Class fix: every cleared verdict carries evidence, so auto-close always has a
    # file path to cite. The evidence here is the aq row itself.
    probe_name, aq_id = parse_probe_ref(detail)
    evidence = {"cmd": None, "clear_when_exit": None, "exit_code": None,
                "kind": "aq_row", "probe": probe_name, "aq_id": aq_id,
                "checked_at": (now if now is not None else time.time())}
    try:
        conn = sqlite3.connect(f"file:{aq_db or AQ_DB}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("SELECT status, resolved_ts, note FROM alerts WHERE id=?",
                               (aq_id,)).fetchone()
        finally:
            conn.close()
        if row:
            evidence.update({"aq_status": row[0], "aq_resolved_ts": row[1],
                             "aq_note": (row[2] or "")[:300]})
    except sqlite3.Error as e:
        evidence["aq_read_error"] = repr(e)
    return True, evidence


def runtime_exceeded(start_ts, max_runtime_s=DEFAULT_MAX_RUNTIME_S, now=None):
    """True once a fire-response ticket has run longer than max_runtime_s wall-clock
    since start_ts (the whole diagnose/hold/apply run, not one child's wall-clock) —
    the hard cap wq#1234 adds so a churning fire ticket blocks for a human instead of
    running 13-22h like wq#1154/#1160/#1161 did."""
    now = now if now is not None else time.time()
    return (now - start_ts) > max_runtime_s
