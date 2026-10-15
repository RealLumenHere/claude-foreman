"""wq#3965 -- per-workspace reconstruction obligation ledger.

An obligation is an RE question the investigation has raised and must still answer
("what does the 0x3c handler do with the length byte?", "is the second partition
signed?"). The agent loop's verify step (design/wq3806_limpet_architecture.md §6)
calls ``require_done`` and refuses to call an investigation done while any
obligation is still ``open``: each one is either ``resolved`` with evidence or
``waived`` with a stated reason.

Shape borrowed from REA's residual-unknown / obligation tools (inspected live via
mcp__rea__record_unknown, update_unknown, verify_unknown_resolution,
build_reconstruction_obligation_ledger):

  - a ``verified`` resolution needs a rationale AND at least one evidence ref
    (REA: ``evidence_ids`` minItems 1); a waiver needs only a rationale, because
    REA treats withdrawn/out-of-scope as "not truth claims", and so do we;
  - every mutation bumps a per-obligation ``revision`` and appends to ``history``;
    callers may pass ``expected_revision`` so a stale writer fails instead of
    overwriting newer analysis (REA ``update_unknown``).

The ledger lives at ``<workspace>/obligations.json`` and each mutation is committed
to the workspace git repo (``workspace.git`` or ``.git``) when one exists, so the
ledger travels with the workspace and its history is replayable.

An evidence ref is either a REA evidence id (``ev_<64 hex>``) or a workspace-relative
path that exists inside the workspace, optionally with a ``#locator`` suffix
(``evidence/ghidra/fn_3c.c#L40``). Absolute paths and paths escaping the workspace
are rejected, so a resolution cannot point at evidence the bundle does not carry.
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import re
import subprocess
from contextlib import contextmanager

LEDGER_NAME = "obligations.json"
SCHEMA_VERSION = "1"
STATUSES = ("open", "resolved", "waived")
SEVERITIES = ("low", "medium", "high", "critical")
_REA_EVIDENCE_ID = re.compile(r"^ev_[a-f0-9]{64}$")


class ObligationError(Exception):
    """A refused ledger operation (bad input, missing evidence, stale revision)."""


class ObligationsOpen(ObligationError):
    """Raised by ``require_done`` while any obligation is still open."""

    def __init__(self, open_items):
        self.open_items = open_items
        ids = ", ".join(o["id"] for o in open_items)
        super().__init__(f"{len(open_items)} open obligation(s) block done: {ids}")


def _now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ledger_path(workspace: str) -> str:
    return os.path.join(os.path.abspath(workspace), LEDGER_NAME)


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "obligations": []}


def load(workspace: str) -> dict:
    """Read the ledger; a workspace without one has an empty ledger."""
    path = ledger_path(workspace)
    if not os.path.exists(path):
        return _empty()
    try:
        with open(path, encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ObligationError(f"cannot read {LEDGER_NAME}: {exc}") from exc
    if not isinstance(ledger, dict) or not isinstance(ledger.get("obligations"), list):
        raise ObligationError(f"{LEDGER_NAME} is not a ledger object with an obligations list")
    return ledger


def _write(workspace: str, ledger: dict) -> None:
    path = ledger_path(workspace)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


@contextmanager
def _locked(workspace: str):
    # Lock the workspace directory itself: no stray lock file for `git add -A` to sweep up.
    fd = os.open(os.path.abspath(workspace), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _git_base(workspace: str) -> list[str] | None:
    ws = os.path.abspath(workspace)
    if os.path.exists(os.path.join(ws, ".git")):
        return ["git", "-C", ws]
    if os.path.isdir(os.path.join(ws, "workspace.git")):
        return ["git", "--git-dir", os.path.join(ws, "workspace.git"), "--work-tree", ws]
    return None


def _commit(workspace: str, message: str) -> bool:
    """Commit only obligations.json; other staged work in the workspace is left alone."""
    base = _git_base(workspace)
    if base is None:
        return False
    ident = []
    probe = subprocess.run(base + ["config", "user.email"], capture_output=True, text=True)
    if not probe.stdout.strip():
        ident = ["-c", "user.name=limpet", "-c", "user.email=limpet@localhost"]
    git = [base[0]] + ident + base[1:]
    if base[1] == "-C":
        rel = LEDGER_NAME
    else:
        rel = ledger_path(workspace)
    add = subprocess.run(git + ["add", "--", rel], capture_output=True, text=True)
    if add.returncode != 0:
        raise ObligationError(f"git add {LEDGER_NAME} failed: {add.stderr.strip()}")
    res = subprocess.run(git + ["commit", "-q", "-m", message, "--", rel],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise ObligationError(f"git commit {LEDGER_NAME} failed: {res.stderr.strip() or res.stdout.strip()}")
    return True


def validate_evidence_ref(workspace: str, ref: str) -> str:
    """Return ``ref`` normalized, or raise if it does not name real workspace evidence."""
    if not isinstance(ref, str) or not ref.strip():
        raise ObligationError("empty evidence ref")
    ref = ref.strip()
    if _REA_EVIDENCE_ID.match(ref):
        return ref
    path, _, locator = ref.partition("#")
    if os.path.isabs(path):
        raise ObligationError(f"evidence ref must be workspace-relative, not absolute: {ref}")
    ws = os.path.realpath(workspace)
    full = os.path.realpath(os.path.join(ws, path))
    if full != ws and not full.startswith(ws + os.sep):
        raise ObligationError(f"evidence ref escapes the workspace: {ref}")
    if full == ws or not os.path.exists(full):
        raise ObligationError(f"evidence ref not found in workspace: {ref}")
    norm = os.path.relpath(full, ws)
    return f"{norm}#{locator}" if locator else norm


def _refs(workspace: str, refs) -> list[str]:
    return [validate_evidence_ref(workspace, r) for r in (refs or [])]


def _find(ledger: dict, ob_id: str) -> dict:
    for ob in ledger["obligations"]:
        if ob.get("id") == ob_id:
            return ob
    raise ObligationError(f"no such obligation: {ob_id}")


def _next_id(ledger: dict) -> str:
    nums = [int(m.group(1)) for ob in ledger["obligations"]
            if (m := re.match(r"^OB-(\d+)$", str(ob.get("id", ""))))]
    return f"OB-{(max(nums) + 1 if nums else 1):03d}"


def _check_revision(ob: dict, expected_revision: int | None) -> None:
    if expected_revision is not None and ob["revision"] != expected_revision:
        raise ObligationError(
            f"{ob['id']} is at revision {ob['revision']}, not {expected_revision}: "
            "re-read the ledger before writing")


def open_obligation(workspace: str, claim: str, *, opened_by: str, evidence=(),
                    severity: str = "medium", by: str = "agent", commit: bool = True,
                    now: str | None = None) -> dict:
    """Record a new open obligation. ``opened_by`` names the analysis step that raised it."""
    if not claim or not claim.strip():
        raise ObligationError("an obligation needs a claim or question")
    if not opened_by or not opened_by.strip():
        raise ObligationError("an obligation needs opened_by (the step that raised it)")
    if severity not in SEVERITIES:
        raise ObligationError(f"severity must be one of {', '.join(SEVERITIES)}")
    refs = _refs(workspace, evidence)
    ts = now or _now()
    with _locked(workspace):
        ledger = load(workspace)
        ob = {
            "id": _next_id(ledger),
            "claim": claim.strip(),
            "opened_by": opened_by.strip(),
            "opened_at": ts,
            "severity": severity,
            "evidence": refs,
            "status": "open",
            "resolution": None,
            "revision": 1,
            "history": [{"revision": 1, "ts": ts, "action": "open", "by": by}],
        }
        ledger["obligations"].append(ob)
        _write(workspace, ledger)
        if commit:
            _commit(workspace, f"obligations: open {ob['id']}: {ob['claim'][:60]}")
    return ob


def _close(workspace: str, ob_id: str, *, status: str, disposition: str, rationale: str,
           evidence, by: str, expected_revision: int | None, commit: bool,
           now: str | None) -> dict:
    if not rationale or not rationale.strip():
        raise ObligationError(f"{status} needs a rationale")
    refs = _refs(workspace, evidence)
    if status == "resolved" and not refs:
        raise ObligationError(
            f"cannot resolve {ob_id} without evidence: pass at least one evidence ref "
            "(workspace path or REA ev_ id), or waive it with a reason")
    ts = now or _now()
    with _locked(workspace):
        ledger = load(workspace)
        ob = _find(ledger, ob_id)
        _check_revision(ob, expected_revision)
        if ob["status"] != "open":
            raise ObligationError(f"{ob_id} is already {ob['status']}")
        ob["status"] = status
        ob["resolution"] = {"disposition": disposition, "rationale": rationale.strip(),
                            "evidence": refs, "ts": ts, "by": by}
        ob["revision"] += 1
        ob["history"].append({"revision": ob["revision"], "ts": ts, "action": status, "by": by})
        _write(workspace, ledger)
        if commit:
            _commit(workspace, f"obligations: {status} {ob_id}")
    return ob


def resolve(workspace: str, ob_id: str, *, evidence, rationale: str, by: str = "agent",
            expected_revision: int | None = None, commit: bool = True,
            now: str | None = None) -> dict:
    """Close an obligation as answered. Refused without at least one real evidence ref."""
    return _close(workspace, ob_id, status="resolved", disposition="verified",
                  rationale=rationale, evidence=evidence, by=by,
                  expected_revision=expected_revision, commit=commit, now=now)


def waive(workspace: str, ob_id: str, *, rationale: str, evidence=(), by: str = "agent",
          expected_revision: int | None = None, commit: bool = True,
          now: str | None = None) -> dict:
    """Close an obligation without answering it (out of scope, withdrawn). Not a truth claim."""
    return _close(workspace, ob_id, status="waived", disposition="waived",
                  rationale=rationale, evidence=evidence, by=by,
                  expected_revision=expected_revision, commit=commit, now=now)


def list_obligations(workspace: str, status: str | None = None) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise ObligationError(f"status must be one of {', '.join(STATUSES)}")
    obs = load(workspace)["obligations"]
    return [o for o in obs if status is None or o.get("status") == status]


def summary(ledger: dict) -> dict:
    counts = {s: 0 for s in STATUSES}
    for ob in ledger["obligations"]:
        counts[ob.get("status", "open") if ob.get("status") in STATUSES else "open"] += 1
    return {"total": sum(counts.values()), **counts, "done_ready": counts["open"] == 0}


def check_done(workspace: str) -> dict:
    """The verify-step gate: ``ok`` only when no obligation is open."""
    ledger = load(workspace)
    open_items = [o for o in ledger["obligations"] if o.get("status") not in ("resolved", "waived")]
    return {"ok": not open_items, "open": open_items, "summary": summary(ledger)}


def require_done(workspace: str) -> dict:
    """Raise ``ObligationsOpen`` unless the workspace may be called done."""
    result = check_done(workspace)
    if not result["ok"]:
        raise ObligationsOpen(result["open"])
    return result
