"""wq#1697 fixes 2+3: make a capability refusal visible as ONE keyed alert-queue row that
carries the exact fleet-exec-policy.yaml diff needed.

Before this, a dispatcher `needs_unresolved` skip only logged and pinged per ticket, and a
CAPABILITY_DENIED block on a P0 (wq#1672: refused 13x) parked with nothing an orchestrator
could apply in one step. The row does not grant anything -- policy stays proposal-by-PR
(BROKER_GRANT_COVERAGE.md); the row is the visible, deduped, copy-pasteable proposal.

Keyed per PATH (not per ticket): ten tickets that need the same root are one grant, and
aq's (source, key) dedup bumps the open row instead of duplicating it. A path the hard-coded
deny-list forbids is reported as NOT grantable rather than proposed. Fail-open throughout:
a missing aq binary must never break a dispatch pass or a failure record.
"""
import hashlib
import os
import re
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import capability_lint  # noqa: E402

AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq")
AQ_SOURCE = "foreman-capability"
POLICY_FILE = "harness/fleet-exec-policy.yaml"
PROPOSAL_MAX_PRI = 1  # wq pri 0/1 = P0/P1

_UNRESOLVED_DIR_RE = re.compile(r"^dir\((?P<path>[^)]+)\) is not under allow\.read_dirs")
_DIR_NEED_RE = re.compile(r"^dir\((?P<path>.+)\)$")


def unresolved_dir_paths(unresolved):
    """Paths from lint `unresolved` lines that a read_dirs grant would resolve. A cited path
    that does not exist (or a stale sweep) is not fixed by a grant, so it is not proposed."""
    out = []
    for line in unresolved or []:
        m = _UNRESOLVED_DIR_RE.match(line)
        if m and m.group("path") not in out:
            out.append(m.group("path"))
    return out


def grant_key(path):
    return f"grant-dir-{hashlib.sha1(path.encode()).hexdigest()[:12]}"


def policy_diff(paths, ticket_id=None):
    """The exact allow.read_dirs addition, as a diff an orchestrator can apply."""
    who = f"  # wq#{ticket_id}" if ticket_id else ""
    lines = [f"--- a/{POLICY_FILE}", f"+++ b/{POLICY_FILE}", "@@ allow.read_dirs @@"]
    lines += [f"+    - {p}{who}" for p in paths]
    return "\n".join(lines)


def aq_add(key, title, body, aq_bin=None):
    aq_bin = aq_bin or AQ_BIN
    if not os.path.exists(aq_bin):
        return False
    try:
        p = subprocess.run([aq_bin, "add", AQ_SOURCE, key, title, body],
                           capture_output=True, text=True, timeout=15)
        return p.returncode == 0
    except Exception:  # noqa: BLE001 — see module docstring
        return False


def file_dir_grant(path, ticket_id, aq_bin=None):
    """One row for one unresolved read root."""
    body = (f"wq#{ticket_id} cites {path}, which is not under allow.read_dirs, so the "
            f"dispatcher skips it every pass. Sweep it (harness/read_dir_sweep.py), then "
            f"propose by PR:\n{policy_diff([path], ticket_id)}")
    return aq_add(grant_key(path), f"grant read root {path} (wq#{ticket_id} needs_unresolved)",
                  body, aq_bin)


def priority_ticket(ticket):
    pri = (ticket or {}).get("pri")
    return isinstance(pri, int) and pri <= PROPOSAL_MAX_PRI


def denied_dir_paths(inferred_needs):
    out = []
    for need in inferred_needs or []:
        m = _DIR_NEED_RE.match(need)
        if m:
            out.append(m.group("path"))
    return out


def denied_row_body(ticket_id, record, failure_path=None):
    """The exact diff for the dir() needs a CAPABILITY_DENIED failure inferred, split into
    grantable roots and hard-denied ones (which no policy PR may grant)."""
    grantable, forbidden = [], []
    for p in denied_dir_paths(record.get("inferred_needs")):
        why = capability_lint.dir_denied(p)
        (forbidden.append(f"{p} ({why})") if why else grantable.append(p))
    other = [n for n in record.get("inferred_needs") or [] if not _DIR_NEED_RE.match(n)]
    parts = [f"wq#{ticket_id} blocked CAPABILITY_DENIED: {record.get('summary', '')}"]
    if grantable:
        parts.append("Apply (then re-run the ticket):\n" + policy_diff(grantable, ticket_id))
    if forbidden:
        parts.append("NOT grantable (hard deny-list; rewrite the ticket instead): "
                     + "; ".join(forbidden))
    if other:
        parts.append("Non-dir needs (see BROKER_GRANT_COVERAGE.md): " + ", ".join(other))
    if not (grantable or forbidden or other):
        parts.append("No mappable need was inferred; read the failure record.")
    if failure_path:
        parts.append(f"failure record: {failure_path}")
    return "\n".join(parts)


def file_denied_row(ticket, record, failure_path=None, aq_bin=None):
    """Open the aq row for a P0/P1 CAPABILITY_DENIED block. Returns True when filed."""
    if not priority_ticket(ticket) or record.get("triage") != "CAPABILITY_DENIED":
        return False
    tid = ticket.get("id")
    return aq_add(f"capability-denied-wq{tid}",
                  f"wq#{tid} (P{ticket['pri']}) blocked CAPABILITY_DENIED",
                  denied_row_body(tid, record, failure_path), aq_bin)
