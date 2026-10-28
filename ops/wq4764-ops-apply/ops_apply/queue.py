"""Request queue: one JSON file per request under
`<queue_dir>/wq<ticket>/<req>.json` (design §3.3/§3.4: "the request goes
STAGED in /var/lib/ops-apply/queue/wq<N>/<req>.json (root-owned; the worker
gets only the req id back)"). Every mutation is a whole-file atomic
`os.replace` under an `flock` on the file itself, so two racing readers
(F5/G9: two resumes, or a worker racing an approver) never interleave a
read-modify-write.

A request record doubles as the V7 approval record: it carries digest,
expires_at and consumed_at directly, so `validation.check_v7_approval` can
be handed the loaded record as-is.
"""
import fcntl
import json
import os
import time
import uuid

STATE_STAGED = "STAGED"
STATE_APPROVED = "APPROVED"
STATE_APPLYING = "APPLYING"
STATE_APPLIED = "APPLIED"
STATE_ROLLED_BACK = "ROLLED_BACK"
STATE_ROLLBACK_FAILED = "ROLLBACK_FAILED"
STATE_REFUSED = "REFUSED"
STATE_EXPIRED = "EXPIRED"
STATE_INTERRUPTED = "INTERRUPTED"

APPROVAL_TTL_S = 24 * 3600


def _req_dir(queue_dir, ticket):
    return os.path.join(queue_dir, f"wq{ticket}")


def _req_path(queue_dir, ticket, req):
    return os.path.join(_req_dir(queue_dir, ticket), f"{req}.json")


def new_req_id():
    return uuid.uuid4().hex


def _atomic_write(path, record):
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(record, f, sort_keys=True, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def stage(queue_dir, ticket, manifest, digest, *, now=None):
    """Write a new STAGED request. Returns the req id."""
    now = time.time() if now is None else now
    req = new_req_id()
    os.makedirs(_req_dir(queue_dir, ticket), exist_ok=True)
    record = {
        "req": req, "ticket": ticket, "manifest": manifest, "digest": digest,
        "state": STATE_STAGED, "created_at": now,
        "approved_by": None, "approved_at": None, "expires_at": None,
        "consumed_at": None, "result": None, "backup": None,
        "inverse_manifest": None,
    }
    _atomic_write(_req_path(queue_dir, ticket, req), record)
    return req


def stage_with_id(queue_dir, ticket, req, manifest, digest, *, state=STATE_STAGED,
                   now=None, **extra):
    """Like stage(), but the caller names the req id — used by the node
    daemon to mirror the gate's req id locally (G7 crash recovery keys off
    this record), with extra fields (config_path, pre_apply_sha, ...) the
    gate-side record never carries."""
    now = time.time() if now is None else now
    os.makedirs(_req_dir(queue_dir, ticket), exist_ok=True)
    record = {
        "req": req, "ticket": ticket, "manifest": manifest, "digest": digest,
        "state": state, "created_at": now,
        "approved_by": None, "approved_at": None, "expires_at": None,
        "consumed_at": None, "result": None, "backup": None,
        "inverse_manifest": None,
    }
    record.update(extra)
    _atomic_write(_req_path(queue_dir, ticket, req), record)
    return record


def load(queue_dir, ticket, req):
    path = _req_path(queue_dir, ticket, req)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def _with_lock(path, fn):
    """Open-or-create `path`, flock it exclusively, run fn(record_or_None),
    write back whatever fn returns (unless it returns None, meaning "leave
    unchanged and raise" — fn should raise itself for that), then release."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, "r+") as f:
            fd = None  # fdopen now owns it
            raw = f.read()
            record = json.loads(raw) if raw else None
            new_record = fn(record)
            f.seek(0)
            f.truncate()
            json.dump(new_record, f, sort_keys=True, indent=1)
            f.flush()
            os.fsync(f.fileno())
            return new_record
    finally:
        if fd is not None:
            os.close(fd)


def approve(queue_dir, ticket, req, *, approver_cgroup_text, now=None, ttl=APPROVAL_TTL_S):
    """Set STAGED -> APPROVED with a fresh TTL. Raises ValueError if the
    record is missing or not STAGED. The approver-identity check (V7's
    self-approval guard) is the caller's job (validation.check_v7_approval)
    before this is called — this function just records who/when."""
    now = time.time() if now is None else now
    path = _req_path(queue_dir, ticket, req)

    def _mutate(record):
        if record is None:
            raise ValueError("no such request")
        if record["state"] != STATE_STAGED:
            raise ValueError(f"request state is {record['state']}, not STAGED")
        record["state"] = STATE_APPROVED
        record["approved_by"] = approver_cgroup_text
        record["approved_at"] = now
        record["expires_at"] = now + ttl
        return record

    return _with_lock(path, _mutate)


def consume(queue_dir, ticket, req, digest, *, now=None):
    """Atomically consume the approval: re-checks digest/expiry/consumed
    under the file lock (not just at the earlier, unlocked
    validation.check_v7_approval call) and marks consumed_at. Returns the
    record on success; raises ValueError on any race loss or mismatch, so
    exactly one of two racing consumers wins (design F5/G9)."""
    now = time.time() if now is None else now
    path = _req_path(queue_dir, ticket, req)

    def _mutate(record):
        if record is None:
            raise ValueError("no such request")
        if record["state"] != STATE_APPROVED:
            raise ValueError(f"request state is {record['state']}, not APPROVED")
        if record["consumed_at"] is not None:
            raise ValueError("already consumed")
        if record["digest"] != digest:
            raise ValueError("digest mismatch")
        if record["expires_at"] is None or record["expires_at"] <= now:
            raise ValueError("approval expired")
        record["consumed_at"] = now
        return record

    return _with_lock(path, _mutate)


def set_state(queue_dir, ticket, req, state, **extra):
    path = _req_path(queue_dir, ticket, req)

    def _mutate(record):
        if record is None:
            raise ValueError("no such request")
        record["state"] = state
        record.update(extra)
        return record

    return _with_lock(path, _mutate)


def pending_applying(queue_dir):
    """Every request across every ticket dir currently in APPLYING state —
    used by node_server's crash-recovery scan (G7) on startup."""
    out = []
    if not os.path.isdir(queue_dir):
        return out
    for ticket_dir in sorted(os.listdir(queue_dir)):
        full = os.path.join(queue_dir, ticket_dir)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(full, name)) as f:
                record = json.load(f)
            if record.get("state") == STATE_APPLYING:
                out.append(record)
    return out
