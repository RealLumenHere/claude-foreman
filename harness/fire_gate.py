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
import time

FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
PLANS_DIR = os.environ.get("FOREMAN_PLANS_DIR", os.path.join(FOREMAN_ROOT, "plans"))
APPROVALS_DIR = os.environ.get("FOREMAN_APPROVALS_DIR",
                                os.path.join(FOREMAN_ROOT, "approvals"))
FIRE_TOKEN_FILE = os.environ.get("FOREMAN_FIRE_TOKEN_FILE",
                                  "/home/lumen/.secrets/foreman-fire-approval.token")
DEFAULT_TIMEOUT_HOURS = 24

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
