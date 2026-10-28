import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from ops_apply import node_server, queue
from ops_apply.canonical import file_sha256, manifest_digest
from ops_apply.errors import OpsApplyError

PARAM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["action", "hostname", "port"],
    "properties": {
        "action": {"enum": ["upsert", "remove"]},
        "hostname": {"type": "string", "pattern": r"^[a-z0-9.-]+\.lumennode\.uk$"},
        "port": {"type": "integer", "minimum": 1024, "maximum": 65535},
        "path": {"type": "string"},
    },
}
CATALOG = {
    "policy_sha256": "p" * 64,
    "recipes": [{"id": "cloudflared.ingress.upsert", "version": 1, "sha256": "r" * 64,
                 "nodes": ["bes"], "unit": "cloudflared", "frozen_nodes": [],
                 "param_schema": PARAM_SCHEMA}],
}
POLICY = {"never": {"nodes": [], "restart": {"bes": []}, "sudo_argv_deny": []}}

BASE_DOC = {"tunnel": "abc", "ingress": [{"service": "http_status:404"}]}


def good_manifest(**overrides):
    m = {"recipe": "cloudflared.ingress.upsert", "recipe_version": 1, "node": "bes",
         "ticket": 4764, "params": {"action": "upsert", "hostname": "x.lumennode.uk", "port": 8123},
         "reason": "t"}
    m.update(overrides)
    m["digest"] = manifest_digest(m)
    return m


class FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def fake_runner(argv, **kwargs):
    if argv[:2] == ["systemctl", "is-active"]:
        return FakeCompleted(0, "active\n")
    return FakeCompleted(0, "")


class NodeServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-node-")
        self.config_path = os.path.join(self.tmp, "config.yml")
        with open(self.config_path, "w") as f:
            yaml.safe_dump(BASE_DOC, f)
        self.queue_dir = os.path.join(self.tmp, "queue")
        self.work_dir = os.path.join(self.tmp, "work")
        self.backup_root = os.path.join(self.tmp, "backups")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _server(self, **overrides):
        kwargs = dict(
            catalog=CATALOG, policy=POLICY, policy_sha_actual="p" * 64,
            queue_dir=self.queue_dir, work_dir=self.work_dir, backup_root=self.backup_root,
            verify_fn=lambda **kw: True, allowed_signers_path="/dev/null",
            recipe_file_sha256="r" * 64, pinned_hostname="bes", pinned_machine_id="m1",
            reported_machine_id="m1", never_nodes=[], config_path=self.config_path,
            runner=fake_runner, http_get=lambda url: 200)
        kwargs.update(overrides)
        return node_server.NodeServer(**kwargs)

    def _envelope(self, manifest=None, expires_at=None):
        manifest = manifest or good_manifest()
        return {"manifest": manifest, "digest": manifest["digest"], "signature": "sig",
                "req": "req-1", "expires_at": expires_at or (time.time() + 3600)}

    def test_handle_apply_runs_the_recipe_and_records_applied(self):
        server = self._server()
        result = server.handle_apply(self._envelope())
        self.assertEqual(result["state"], "APPLIED")
        record = queue.load(self.queue_dir, 4764, "req-1")
        self.assertEqual(record["state"], "APPLIED")
        self.assertIsNotNone(record["pre_apply_sha"])

    def test_handle_apply_refuses_signature_failure(self):
        server = self._server(verify_fn=lambda **kw: False)
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_apply(self._envelope())
        self.assertEqual(ctx.exception.code, "OPS_SIGNATURE")

    def test_handle_apply_refuses_expired_approval(self):
        server = self._server()
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_apply(self._envelope(expires_at=time.time() - 10))
        self.assertEqual(ctx.exception.code, "OPS_APPROVAL")

    def test_handle_apply_refuses_frozen_node(self):
        catalog = {"policy_sha256": "p" * 64,
                   "recipes": [dict(CATALOG["recipes"][0], frozen_nodes=["bes"])]}
        server = self._server(catalog=catalog)
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_apply(self._envelope())
        self.assertEqual(ctx.exception.code, "OPS_NOT_ALLOWED")

    def test_handle_apply_refuses_host_mismatch(self):
        server = self._server(pinned_hostname="anubis")
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_apply(self._envelope())
        self.assertEqual(ctx.exception.code, "OPS_HOST")

    def test_recover_marks_dangling_applying_record_interrupted_without_resuming(self):
        pre_sha = file_sha256(self.config_path)
        queue.stage_with_id(self.queue_dir, 4764, "crashed-req", good_manifest(),
                            good_manifest()["digest"], state=queue.STATE_APPLYING,
                            config_path=self.config_path, pre_apply_sha=pre_sha,
                            intended_sha=None)
        server = self._server()
        restored = server.recover()
        record = queue.load(self.queue_dir, 4764, "crashed-req")
        self.assertEqual(record["state"], queue.STATE_INTERRUPTED)
        self.assertEqual(restored, [])  # live file never diverged from pre_apply_sha

    def test_recover_restores_backup_when_live_file_diverged(self):
        pre_sha = file_sha256(self.config_path)
        backup_dir = os.path.join(self.backup_root, "crashed-req")
        os.makedirs(backup_dir, exist_ok=True)
        with open(self.config_path) as src, \
             open(os.path.join(backup_dir, "config.yml"), "w") as dst:
            dst.write(src.read())
        queue.stage_with_id(self.queue_dir, 4764, "crashed-req", good_manifest(),
                            good_manifest()["digest"], state=queue.STATE_APPLYING,
                            config_path=self.config_path, pre_apply_sha=pre_sha,
                            intended_sha=None)
        # simulate the daemon dying after step 7's rename but before recording state
        with open(self.config_path, "w") as f:
            f.write("tunnel: tampered\ningress: [{service: http_status:404}]\n")
        server = self._server()
        restored = server.recover()
        self.assertEqual(restored, ["crashed-req"])
        with open(self.config_path) as f:
            self.assertEqual(yaml.safe_load(f), BASE_DOC)


class NodeServerSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-node-sock-")
        self.config_path = os.path.join(self.tmp, "config.yml")
        with open(self.config_path, "w") as f:
            yaml.safe_dump(BASE_DOC, f)
        self.queue_dir = os.path.join(self.tmp, "queue")
        self.work_dir = os.path.join(self.tmp, "work")
        self.backup_root = os.path.join(self.tmp, "backups")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_over_a_real_socket(self):
        server = node_server.NodeServer(
            catalog=CATALOG, policy=POLICY, policy_sha_actual="p" * 64,
            queue_dir=self.queue_dir, work_dir=self.work_dir, backup_root=self.backup_root,
            verify_fn=lambda **kw: True, allowed_signers_path="/dev/null",
            recipe_file_sha256="r" * 64, pinned_hostname="bes", pinned_machine_id="m1",
            reported_machine_id="m1", never_nodes=[], config_path=self.config_path,
            runner=fake_runner, http_get=lambda url: 200)
        manifest = good_manifest()
        envelope = {"manifest": manifest, "digest": manifest["digest"], "signature": "sig",
                    "req": "req-1", "expires_at": time.time() + 3600}

        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            thread = threading.Thread(target=node_server.serve_one, args=(server, b))
            thread.start()
            a.sendall(json.dumps(envelope).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = a.recv(65536)
                if not chunk:
                    break
                buf += chunk
            thread.join(timeout=5)
            reply = json.loads(buf.decode())
        finally:
            a.close()
            b.close()
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["state"], "APPLIED")


if __name__ == "__main__":
    unittest.main()
