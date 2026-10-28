"""ops-apply-gate: root, zero capabilities, socket-activated
(design §3.2/§3.5). Validates a submitted manifest (V1-V6, V9, V10), stages
it, and — once approved out-of-band by a non-worker identity — re-checks
V7+V8, signs the digest and forwards the signed envelope to the node over
ssh. The gate never executes a recipe itself; it only validates and signs.

The socket-accept loop (`serve_forever`) is a thin shell around
`GateServer`, which holds all the logic and takes every dependency
(catalog, policy, lease lookup, node transport, signer) as a constructor
argument, so a test can drive it with fakes and no real socket, wq db or
ssh (GateServer methods), while a second, smaller test drives the real
AF_UNIX accept loop with a real peer (test_gate_server.py's server tests).
"""
import json
import socket

from . import queue
from .canonical import manifest_digest
from .errors import OpsApplyError
from .identity import peer_cred, read_cgroup, worker_unit_from_cgroup
from .recipes import get_recipe
from .validation import (
    check_v1_identity, check_v2_lease, check_v3_host, check_v4_catalog,
    check_v5_params, check_v6_policy, check_not_self_approval,
    check_v7_approval, check_v8_signature_gate, check_v9_argv, check_v10_digest,
)


class GateServer:
    def __init__(self, *, catalog, policy, policy_sha_actual, queue_dir,
                 lease_lookup, node_transport, sign_fn, recipe_file_sha256,
                 pinned_hostname, pinned_machine_id, reported_machine_id,
                 never_nodes):
        self.catalog = catalog
        self.policy = policy
        self.policy_sha_actual = policy_sha_actual
        self.queue_dir = queue_dir
        self.lease_lookup = lease_lookup
        self.node_transport = node_transport
        self.sign_fn = sign_fn
        self.recipe_file_sha256 = recipe_file_sha256
        self.pinned_hostname = pinned_hostname
        self.pinned_machine_id = pinned_machine_id
        self.reported_machine_id = reported_machine_id
        self.never_nodes = never_nodes

    def _recipe_or_raise(self, manifest):
        recipe = get_recipe(manifest["recipe"], manifest["recipe_version"])
        if recipe is None:
            raise OpsApplyError("OPS_NOT_ALLOWED",
                                 f"no recipe module for {manifest['recipe']}")
        return recipe

    def handle_submit(self, manifest, peer_cgroup_text):
        """V1-V6, V9, V10 (design: "the gate runs V1-V6, V9 and V10, and the
        request goes STAGED"). Returns {"req": <id>} or raises
        OpsApplyError."""
        ticket = check_v1_identity(peer_cgroup_text, manifest["ticket"])
        unit = worker_unit_from_cgroup(peer_cgroup_text)
        lease = self.lease_lookup(ticket)
        check_v2_lease(lease, ticket, unit)
        check_v3_host(manifest["node"], pinned_hostname=self.pinned_hostname,
                      pinned_machine_id=self.pinned_machine_id,
                      reported_machine_id=self.reported_machine_id,
                      never_nodes=self.never_nodes)
        entry = check_v4_catalog(self.catalog, manifest)
        check_v5_params(entry, manifest["params"])
        check_v6_policy(self.policy, policy_sha_actual=self.policy_sha_actual,
                        catalog_policy_sha=self.catalog.get("policy_sha256"),
                        unit=entry["unit"], node=manifest["node"])
        recipe = self._recipe_or_raise(manifest)
        argv_preview = recipe.render_argv(
            "/run/ops-apply-work/preview.yml",
            manifest["params"].get("hostname"), manifest["params"].get("path"))
        check_v9_argv(argv_preview, self.policy)
        digest = check_v10_digest(manifest)
        req = queue.stage(self.queue_dir, manifest["ticket"], manifest, digest)
        return {"req": req, "digest": digest}

    def handle_approve(self, ticket, req, approver_cgroup_text):
        """Out-of-band: an approver (never a worker cgroup) calls this via
        `ops-apply approve <req>`. Records the approval, then — "after
        which the gate runs V7 and V8, signs, and forwards" (design §3.4)
        — immediately runs handle_forward. Approval and forwarding are one
        call from the CLI's point of view; they are two methods internally
        so each half stays independently testable."""
        record = queue.load(self.queue_dir, ticket, req)
        if record is None:
            raise OpsApplyError("OPS_APPROVAL", "no such request")
        check_not_self_approval(approver_cgroup_text)
        queue.approve(self.queue_dir, ticket, req,
                      approver_cgroup_text=approver_cgroup_text)
        return self.handle_forward(ticket, req)

    def handle_forward(self, ticket, req):
        """V7 (re-checked + atomically consumed) + V8 gate-half, sign, then
        hand the signed envelope to `node_transport`."""
        record = queue.load(self.queue_dir, ticket, req)
        if record is None:
            raise OpsApplyError("OPS_APPROVAL", "no such request")
        check_v7_approval(record, record["digest"])
        entry = check_v4_catalog(self.catalog, record["manifest"])
        check_v8_signature_gate(entry, self.recipe_file_sha256)
        queue.consume(self.queue_dir, ticket, req, record["digest"])
        signature = self.sign_fn(record["digest"])
        envelope = {"manifest": record["manifest"], "digest": record["digest"],
                    "signature": signature, "req": req,
                    "expires_at": record["expires_at"]}
        response = self.node_transport(envelope)
        queue.set_state(self.queue_dir, ticket, req, response["state"],
                        result=response)
        return response

    def handle_status(self, ticket, req):
        record = queue.load(self.queue_dir, ticket, req)
        if record is None:
            raise OpsApplyError("OPS_APPROVAL", "no such request")
        return record

    def handle_revert(self, ticket, req, peer_cgroup_text):
        record = queue.load(self.queue_dir, ticket, req)
        if record is None:
            raise OpsApplyError("OPS_APPROVAL", "no such request")
        if record.get("inverse_manifest") is None:
            raise OpsApplyError("OPS_APPROVAL", "request has no inverse manifest yet")
        inverse = dict(record["inverse_manifest"])
        inverse["digest"] = manifest_digest(inverse)
        return self.handle_submit(inverse, peer_cgroup_text)


# ---------------------------------------------------------------------------
# socket accept loop
# ---------------------------------------------------------------------------

def _read_line(conn):
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf.decode()


def serve_one(server, conn, *, cgroup_reader=read_cgroup):
    """Handle exactly one newline-delimited JSON request on an already-
    accepted connection. Split out from the accept loop so a test can drive
    it with a real socketpair and a real peer cgroup, without a systemd
    socket-activation unit."""
    pid, _uid, _gid = peer_cred(conn)
    cgroup_text = cgroup_reader(pid)
    raw = _read_line(conn)
    try:
        req = json.loads(raw)
        verb = req["verb"]
        if verb == "submit":
            result = server.handle_submit(req["manifest"], cgroup_text)
        elif verb == "approve":
            result = server.handle_approve(req["ticket"], req["req"], cgroup_text)
        elif verb == "status":
            result = server.handle_status(req["ticket"], req["req"])
        elif verb == "revert":
            result = server.handle_revert(req["ticket"], req["req"], cgroup_text)
        else:
            conn.sendall(json.dumps({"ok": False, "code": "OPS_NOT_ALLOWED",
                                      "error": f"unknown verb {verb!r}"}).encode() + b"\n")
            return
        conn.sendall(json.dumps({"ok": True, **result}).encode() + b"\n")
    except OpsApplyError as e:
        conn.sendall(json.dumps({"ok": False, "code": e.code, "error": str(e)}).encode() + b"\n")
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        conn.sendall(json.dumps({"ok": False, "code": "OPS_PARAMS",
                                  "error": f"malformed request: {e}"}).encode() + b"\n")


def serve_forever(server, sock_path):  # pragma: no cover - thin I/O shell
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(sock_path)
    sock.listen(8)
    try:
        while True:
            conn, _ = sock.accept()
            try:
                serve_one(server, conn)
            finally:
                conn.close()
    finally:
        sock.close()
