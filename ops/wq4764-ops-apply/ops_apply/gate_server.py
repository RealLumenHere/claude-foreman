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

from . import cas, queue
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
                 never_nodes, cas_root=None):
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
        self.cas_root = cas_root

    def _stage_artifacts(self, manifest, artifact_content):
        """wq#4829: every artifact the manifest declares goes into the CAS
        by its declared digest BEFORE staging. The declared digest lives
        inside the manifest, so it is covered by V10's manifest digest and
        later by the approval signature; the bytes themselves are pinned by
        the CAS path. Content that hashes to anything other than its
        declared digest is refused (OPS_CAS), as is a declared artifact
        with no content and no existing blob — fail closed, nothing staged."""
        artifacts = manifest.get("artifacts") or {}
        if not artifacts:
            return
        if self.cas_root is None:
            raise OpsApplyError("OPS_CAS",
                                 "manifest declares artifacts but gate has no cas_root")
        artifact_content = artifact_content or {}
        for name, declared in artifacts.items():
            content = artifact_content.get(name)
            if content is None:
                # dedupe path: no bytes resent, the blob must already exist
                cas.verify(self.cas_root, declared)
            else:
                stored = cas.store(self.cas_root, content)
                if stored != declared:
                    raise OpsApplyError(
                        "OPS_CAS",
                        f"artifact {name!r} declares {declared} but content "
                        f"hashes to {stored}")
            cas.record_artifact(self.cas_root, manifest["ticket"], name, declared)

    def _recipe_or_raise(self, manifest):
        recipe = get_recipe(manifest["recipe"], manifest["recipe_version"])
        if recipe is None:
            raise OpsApplyError("OPS_NOT_ALLOWED",
                                 f"no recipe module for {manifest['recipe']}")
        return recipe

    def handle_submit(self, manifest, peer_cgroup_text, artifact_content=None):
        """V1-V6, V9, V10 (design: "the gate runs V1-V6, V9 and V10, and the
        request goes STAGED"), then CAS-stages any declared artifacts
        (wq#4829). Returns {"req": <id>} or raises OpsApplyError."""
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
        self._stage_artifacts(manifest, artifact_content)
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
        # wq#4829: re-read every declared artifact through cas.retrieve —
        # a verified read, so bytes tampered between submit and approval are
        # refused here, not shipped — then stamp the approval record onto
        # the ticket manifest and forward the verified bytes to the node.
        artifact_content = {}
        for name, art_digest in (record["manifest"].get("artifacts") or {}).items():
            if self.cas_root is None:
                raise OpsApplyError("OPS_CAS",
                                     "manifest declares artifacts but gate has no cas_root")
            artifact_content[name] = cas.retrieve(self.cas_root, art_digest).decode()
            cas.record_approval(self.cas_root, ticket, art_digest,
                                 {"approved_by": record["approved_by"],
                                  "approved_at": record["approved_at"],
                                  "manifest_digest": record["digest"], "req": req})
        envelope = {"manifest": record["manifest"], "digest": record["digest"],
                    "signature": signature, "req": req,
                    "expires_at": record["expires_at"]}
        if artifact_content:
            envelope["artifact_content"] = artifact_content
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
            result = server.handle_submit(req["manifest"], cgroup_text,
                                           artifact_content=req.get("artifact_content"))
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
