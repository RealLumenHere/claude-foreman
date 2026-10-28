"""ops-apply-node: root, CapabilityBoundingSet CAP_CHOWN CAP_FOWNER
CAP_DAC_OVERRIDE (design §3.2/§3.5), reached only via a forced-command ssh
key (`authorized_keys: restrict,command=".../ops-apply-node"`). Re-runs
V3-V10 against its OWN copies of the catalog and policy (never trusting the
gate's say-so for anything the node can check itself), verifies the gate's
signature, re-renders and re-checks every argv immediately before it execs
(V9, "re-checked immediately before each exec"), and then runs the recipe.

G7 crash recovery: on start, `recover()` scans the queue for APPLYING
records left by a daemon that died mid-apply, compares the live file's sha
against the backup and the pre-apply sha, restores the backup if the live
file is neither the pre- nor the intended post-state, and marks the record
INTERRUPTED. It never resumes an apply.
"""
import json
import os
import socket

from . import queue
from .canonical import file_sha256
from .errors import OpsApplyError
from .recipes import get_recipe
from .validation import (
    check_v3_host, check_v4_catalog, check_v5_params, check_v6_policy,
    check_v7_approval, check_v8_signature_node, check_v9_argv,
    check_v10_digest,
)


class NodeServer:
    def __init__(self, *, catalog, policy, policy_sha_actual, queue_dir,
                 work_dir, backup_root, verify_fn, allowed_signers_path,
                 recipe_file_sha256, pinned_hostname, pinned_machine_id,
                 reported_machine_id, never_nodes, config_path=None,
                 runner=None, clock=None, http_get=None):
        self.catalog = catalog
        self.policy = policy
        self.policy_sha_actual = policy_sha_actual
        self.queue_dir = queue_dir
        self.work_dir = work_dir
        self.backup_root = backup_root
        self.verify_fn = verify_fn
        self.allowed_signers_path = allowed_signers_path
        self.recipe_file_sha256 = recipe_file_sha256
        self.pinned_hostname = pinned_hostname
        self.pinned_machine_id = pinned_machine_id
        self.reported_machine_id = reported_machine_id
        self.never_nodes = never_nodes
        self.config_path = config_path
        self._runner = runner
        self._clock = clock
        self._http_get = http_get

    def _revalidate(self, manifest, digest, signature, expires_at):
        check_v3_host(manifest["node"], pinned_hostname=self.pinned_hostname,
                      pinned_machine_id=self.pinned_machine_id,
                      reported_machine_id=self.reported_machine_id,
                      never_nodes=self.never_nodes)
        entry = check_v4_catalog(self.catalog, manifest)
        check_v5_params(entry, manifest["params"])
        check_v6_policy(self.policy, policy_sha_actual=self.policy_sha_actual,
                        catalog_policy_sha=self.catalog.get("policy_sha256"),
                        unit=entry["unit"], node=manifest["node"])
        check_v7_approval({"digest": digest, "expires_at": expires_at, "consumed_at": None},
                          digest)
        check_v8_signature_node(self.verify_fn, digest=digest, signature=signature,
                                allowed_signers_path=self.allowed_signers_path)
        recipe = get_recipe(manifest["recipe"], manifest["recipe_version"])
        if recipe is None:
            raise OpsApplyError("OPS_NOT_ALLOWED", f"no recipe module for {manifest['recipe']}")
        argv_preview = recipe.render_argv("/run/ops-apply-work/preview.yml",
                                           manifest["params"].get("hostname"),
                                           manifest["params"].get("path"))
        check_v9_argv(argv_preview, self.policy)
        check_v10_digest(manifest, approved_digest=digest)
        return entry, recipe

    def handle_apply(self, envelope):
        manifest = envelope["manifest"]
        digest = envelope["digest"]
        req = envelope["req"]
        ticket = manifest["ticket"]
        _entry, recipe = self._revalidate(manifest, digest, envelope["signature"],
                                           envelope["expires_at"])
        config_path = self.config_path or recipe.TARGET_PATH
        try:
            pre_apply_sha = file_sha256(config_path)
        except OSError:
            pre_apply_sha = None
        # G7: record APPLYING with the pre-apply sha *before* touching
        # anything, so a crash mid-apply leaves recover() a known-good
        # baseline to compare the live file against.
        queue.stage_with_id(self.queue_dir, ticket, req, manifest, digest,
                            state=queue.STATE_APPLYING, config_path=config_path,
                            pre_apply_sha=pre_apply_sha, intended_sha=None)
        kwargs = dict(manifest=manifest, work_dir=self.work_dir,
                      backup_root=self.backup_root, req_id=req, config_path=config_path)
        if self._runner is not None:
            kwargs["runner"] = self._runner
        if self._clock is not None:
            kwargs["clock"] = self._clock
        if self._http_get is not None:
            kwargs["http_get"] = self._http_get
        result = recipe.apply(**kwargs)
        try:
            post_sha = file_sha256(config_path)
        except OSError:
            post_sha = None
        queue.set_state(self.queue_dir, ticket, req, result.status,
                        backup=result.backup_path, intended_sha=post_sha,
                        inverse_manifest=result.inverse_manifest, detail=result.detail)
        return {"state": result.status, "detail": result.detail,
                "backup": result.backup_path, "inverse_manifest": result.inverse_manifest}

    def recover(self):
        """G7: never resumes an apply. For each APPLYING record left by a
        daemon that died mid-apply, decide whether the live file is
        pre-state, intended post-state, or neither — only the "neither"
        case restores the backup — then mark INTERRUPTED either way. The
        backup path is recomputed from backup_root/req, not trusted from
        the record (which may predate step 5 having run at all)."""
        restored = []
        for record in queue.pending_applying(self.queue_dir):
            config_path = record.get("config_path")
            pre_sha = record.get("pre_apply_sha")
            intended_sha = record.get("intended_sha")
            if config_path and pre_sha is not None:
                backup_path = os.path.join(self.backup_root, record["req"],
                                            os.path.basename(config_path))
                try:
                    live_sha = file_sha256(config_path)
                except OSError:
                    live_sha = None
                if live_sha not in (pre_sha, intended_sha) and os.path.exists(backup_path):
                    with open(backup_path, "rb") as src, open(config_path, "wb") as dst:
                        dst.write(src.read())
                    restored.append(record["req"])
            queue.set_state(self.queue_dir, record["ticket"], record["req"],
                            queue.STATE_INTERRUPTED)
        return restored


# ---------------------------------------------------------------------------
# socket accept loop — reached only through the forced-command ssh wrapper,
# so the only peer this ever sees is the gate (or, interactively, an
# operator debugging on the node itself).
# ---------------------------------------------------------------------------

def serve_one(server, conn):
    raw = b""
    while not raw.endswith(b"\n"):
        chunk = conn.recv(65536)
        if not chunk:
            break
        raw += chunk
    try:
        envelope = json.loads(raw.decode())
        result = server.handle_apply(envelope)
        conn.sendall(json.dumps({"ok": True, **result}).encode() + b"\n")
    except OpsApplyError as e:
        conn.sendall(json.dumps({"ok": False, "code": e.code, "error": str(e)}).encode() + b"\n")
    except (KeyError, ValueError) as e:
        conn.sendall(json.dumps({"ok": False, "code": "OPS_PARAMS",
                                  "error": f"malformed envelope: {e}"}).encode() + b"\n")


def serve_forever(server, sock_path):  # pragma: no cover - thin I/O shell
    server.recover()
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
