"""V1 identity primitives: SO_PEERCRED on the accepted connection, then the
peer pid's cgroup (design §3.4 V1, same shape as token-engine's daemon —
token-engine/token_engine_daemon.py:1702 — and hold_gate's worker-session
check). Kept free of socket I/O in the parsing half so it is unit-testable
without a real UNIX socket.
"""
import re
import socket
import struct

WORKER_UNIT_RE = re.compile(r"(lumen-job-fworker-wq(\d+)-[^/\s]*\.service)")
WORKER_SLICE_MARKER = "lumen-workers.slice"


def peer_cred(conn):
    """(pid, uid, gid) of the process on the other end of `conn`."""
    data = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                            struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", data)
    return pid, uid, gid


def read_cgroup(pid, *, proc_root="/proc"):
    with open(f"{proc_root}/{pid}/cgroup", "r") as f:
        return f.read()


def worker_ticket_from_cgroup(cgroup_text):
    """Ticket number N if `cgroup_text` sits under
    lumen-workers.slice/lumen-job-fworker-wqN-*.service, else None. A peer
    outside the worker slice (e.g. the orchestrator, or `wq grant approve`
    run interactively) returns None — callers use that to tell a worker
    identity from a non-worker approver identity (V7's self-approval guard)."""
    if WORKER_SLICE_MARKER not in cgroup_text:
        return None
    m = WORKER_UNIT_RE.search(cgroup_text)
    if not m:
        return None
    return int(m.group(2))


def worker_unit_from_cgroup(cgroup_text):
    """Full unit name (e.g. "lumen-job-fworker-wq4764-1771999999.service"),
    or None on the same conditions as worker_ticket_from_cgroup. Used to
    match wq's lease_owner column, which stores the full unit name."""
    if WORKER_SLICE_MARKER not in cgroup_text:
        return None
    m = WORKER_UNIT_RE.search(cgroup_text)
    if not m:
        return None
    return m.group(1)
