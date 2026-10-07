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
from datetime import datetime, timezone

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import capability_lint  # noqa: E402

AQ_BIN = os.environ.get("FOREMAN_AQ_BIN", "/home/lumen/bin/aq")
AQ_SOURCE = "foreman-capability"
POLICY_FILE = "harness/fleet-exec-policy.yaml"
PROPOSAL_MAX_PRI = 1  # wq pri 0/1 = P0/P1

# wq#3457: the dispatcher re-files a still-cited root every 24h (its grant_proposals dedup
# window), so a grant-dir row nobody has bumped for 3 days has no live ticket behind it.
STALE_H = 72

_UNRESOLVED_DIR_RE = re.compile(r"^dir\((?P<path>[^)]+)\) is not under allow\.read_dirs")
_DIR_NEED_RE = re.compile(r"^dir\((?P<path>.+)\)$")
_AQ_ROW_RE = re.compile(r"^\[(?P<id>\d+)\]\s+(?P<status>\w+)\s+x(?P<count>\d+)\s+(?P<source>\S+)\s+"
                        r"(?P<key>\S+)\s+owner=\S+\s+(?P<ts>\S+)\s+(?P<title>.*)$")
_GRANT_TITLE_RE = re.compile(r"^grant read root (?P<path>\S+) \(wq#")
_DENIED_KEY_RE = re.compile(r"^capability-denied-wq(?P<tid>\d+)$")


def declined_root(path, policy=None):
    """The `declined_read_dirs` root `path` sits under, or None: a root already reviewed
    and kept off allow.read_dirs, so proposing it again is noise (wq#3457)."""
    policy = capability_lint.load_policy() if policy is None else policy
    for ent in (policy or {}).get("declined_read_dirs") or []:
        root = ent.get("path") if isinstance(ent, dict) else ent
        if not isinstance(root, str):
            continue
        exact = isinstance(ent, dict) and ent.get("exact")
        if (os.path.normpath(path) == os.path.normpath(root) if exact
                else capability_lint._under(path, root)):
            return root
    return None


def unresolved_dir_paths(unresolved, policy=None):
    """Paths from lint `unresolved` lines that a read_dirs grant would resolve. A cited path
    that does not exist (or a stale sweep) is not fixed by a grant, so it is not proposed;
    neither is one under a declined root, since that grant has already been refused."""
    out = []
    for line in unresolved or []:
        m = _UNRESOLVED_DIR_RE.match(line)
        if m and m.group("path") not in out and not declined_root(m.group("path"), policy):
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


# ---- wq#3457: close rows whose proposal is moot ----------------------------------------
# Filing was the only half that existed: a row stayed open after its root was granted,
# declined, deleted, or stopped being cited, and after its blocked ticket moved on, so the
# source piled up to 33 rows. A row is closed only on a fact that makes it moot; a root
# that comes back is re-filed by the dispatcher as a fresh row.

def aq_open_rows(aq_bin=None):
    """This source's open rows from `aq list open`, or None when aq is unreadable."""
    aq_bin = aq_bin or AQ_BIN
    if not os.path.exists(aq_bin):
        return None
    try:
        p = subprocess.run([aq_bin, "list", "open"], capture_output=True, text=True, timeout=60)
    except Exception:  # noqa: BLE001 — see module docstring
        return None
    if p.returncode != 0:
        return None
    rows = []
    for line in p.stdout.splitlines():
        m = _AQ_ROW_RE.match(line)
        if m and m.group("source") == AQ_SOURCE:
            rows.append({"id": int(m.group("id")), "key": m.group("key"), "ts": m.group("ts"),
                         "title": m.group("title").strip()})
    return rows


def aq_resolve(row_id, note, aq_bin=None):
    try:
        p = subprocess.run([aq_bin or AQ_BIN, "resolve", str(row_id), note],
                           capture_output=True, text=True, timeout=15)
        return p.returncode == 0
    except Exception:  # noqa: BLE001 — see module docstring
        return False


def _grant_row_path(row):
    """The path a grant-dir row proposes, or None when `aq list` truncated the title: the
    parsed path must hash back to the row key, so a cut-off path is never acted on."""
    m = _GRANT_TITLE_RE.match(row["title"])
    if m and grant_key(m.group("path")) == row["key"]:
        return m.group("path")
    return None


def _age_h(ts, now):
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - then).total_seconds() / 3600


def moot_reason(row, policy, wq_status, now):
    """Why an open row no longer asks anything of anyone, or None to keep it open.
    `wq_status(tid)` returns the ticket's status, or None when wq could not say."""
    key = row["key"]
    if key.startswith("grant-dir-"):
        path = _grant_row_path(row)
        if path:
            root = capability_lint.allowed_read_dir(path, policy)
            if root:
                return f"granted: {path} is under allow.read_dirs root {root}"
            root = declined_root(path, policy)
            if root:
                return f"declined: {path} is under declined_read_dirs root {root}"
            if not os.path.isdir(path):
                return f"{path} no longer exists"
        age = _age_h(row["ts"], now)
        if age is not None and age > STALE_H:
            return (f"stale: not re-filed for {age:.0f}h; the dispatcher re-files a cited "
                    f"root every 24h, so no dispatch candidate needs it")
        return None
    m = _DENIED_KEY_RE.match(key)
    if m:
        status = wq_status(int(m.group("tid")))
        if status and status != "blocked":
            return f"wq#{m.group('tid')} is {status}, no longer blocked"
    return None


def reconcile(wq_status, policy=None, dry_run=False, now=None, aq_bin=None):
    """Resolve every open row of this source that moot_reason says is moot. Returns
    [(row_id, key, reason, resolved)], or None when aq could not be listed."""
    rows = aq_open_rows(aq_bin)
    if rows is None:
        return None
    policy = capability_lint.load_policy() if policy is None else policy
    now = now or datetime.now(timezone.utc)
    out = []
    for row in rows:
        why = moot_reason(row, policy, wq_status, now)
        if why:
            done = False if dry_run else aq_resolve(row["id"], f"wq#3457 reconcile: {why}", aq_bin)
            out.append((row["id"], row["key"], why, done))
    return out
