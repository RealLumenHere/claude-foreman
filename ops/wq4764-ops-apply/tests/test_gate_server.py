import json
import os
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_apply import gate_server, queue
from ops_apply.canonical import manifest_digest
from ops_apply.errors import OpsApplyError

WORKER_CGROUP = "0::/lumen-workers.slice/lumen-job-fworker-wq4764-111.service\n"
APPROVER_CGROUP = "0::/user.slice/orchestrator.scope\n"
WORKER_UNIT = "lumen-job-fworker-wq4764-111.service"

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


def good_manifest(**overrides):
    m = {"recipe": "cloudflared.ingress.upsert", "recipe_version": 1, "node": "bes",
         "ticket": 4764, "params": {"action": "upsert", "hostname": "x.lumennode.uk", "port": 8123},
         "reason": "t"}
    m.update(overrides)
    m["digest"] = manifest_digest(m)
    return m


_DEFAULT_LEASE = object()


def make_server(tmp, *, lease=_DEFAULT_LEASE, node_transport=None):
    if lease is _DEFAULT_LEASE:
        lease = {"status": "claimed", "lane": "ops-apply", "lease_owner": WORKER_UNIT,
                 "lease_expires_at": 10 ** 12}
    return gate_server.GateServer(
        catalog=CATALOG, policy=POLICY, policy_sha_actual="p" * 64,
        queue_dir=tmp, lease_lookup=lambda ticket: lease,
        node_transport=node_transport or (lambda env: {"state": "APPLIED"}),
        sign_fn=lambda digest: "fake-signature",
        recipe_file_sha256="r" * 64, pinned_hostname="bes", pinned_machine_id="m1",
        reported_machine_id="m1", never_nodes=[])


class GateServerUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-gate-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_submit_stages_a_valid_manifest(self):
        server = make_server(self.tmp)
        result = server.handle_submit(good_manifest(), WORKER_CGROUP)
        record = queue.load(self.tmp, 4764, result["req"])
        self.assertEqual(record["state"], queue.STATE_STAGED)

    def test_submit_refuses_non_worker_peer(self):
        server = make_server(self.tmp)
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_submit(good_manifest(), APPROVER_CGROUP)
        self.assertEqual(ctx.exception.code, "OPS_IDENTITY")

    def test_submit_refuses_without_a_lease(self):
        server = make_server(self.tmp, lease=None)
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_submit(good_manifest(), WORKER_CGROUP)
        self.assertEqual(ctx.exception.code, "OPS_LEASE")

    def test_approve_by_worker_self_is_refused(self):
        server = make_server(self.tmp)
        req = server.handle_submit(good_manifest(), WORKER_CGROUP)["req"]
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_approve(4764, req, WORKER_CGROUP)
        self.assertEqual(ctx.exception.code, "OPS_APPROVAL")

    def test_approve_by_orchestrator_forwards_to_node(self):
        calls = []
        server = make_server(self.tmp, node_transport=lambda env: calls.append(env) or
                             {"state": "APPLIED"})
        req = server.handle_submit(good_manifest(), WORKER_CGROUP)["req"]
        result = server.handle_approve(4764, req, APPROVER_CGROUP)
        self.assertEqual(result["state"], "APPLIED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["signature"], "fake-signature")
        record = queue.load(self.tmp, 4764, req)
        self.assertEqual(record["state"], "APPLIED")
        self.assertIsNotNone(record["consumed_at"])

    def test_double_approve_is_refused_on_the_second_call(self):
        server = make_server(self.tmp)
        req = server.handle_submit(good_manifest(), WORKER_CGROUP)["req"]
        server.handle_approve(4764, req, APPROVER_CGROUP)
        with self.assertRaises(ValueError):
            server.handle_approve(4764, req, APPROVER_CGROUP)

    def test_status_returns_the_staged_record(self):
        server = make_server(self.tmp)
        req = server.handle_submit(good_manifest(), WORKER_CGROUP)["req"]
        status = server.handle_status(4764, req)
        self.assertEqual(status["state"], queue.STATE_STAGED)

    def test_revert_submits_the_inverse_manifest(self):
        server = make_server(self.tmp)
        req = server.handle_submit(good_manifest(), WORKER_CGROUP)["req"]
        queue.set_state(self.tmp, 4764, req, "APPLIED",
                        inverse_manifest={"recipe": "cloudflared.ingress.upsert",
                                          "recipe_version": 1, "node": "bes", "ticket": 4764,
                                          "reason": "t",
                                          "params": {"action": "remove", "hostname": "x.lumennode.uk",
                                                     "port": 8123}})
        result = server.handle_revert(4764, req, WORKER_CGROUP)
        reverted = queue.load(self.tmp, 4764, result["req"])
        self.assertEqual(reverted["manifest"]["params"]["action"], "remove")


class GateServerSocketTests(unittest.TestCase):
    """Drives the real serve_one()/AF_UNIX accept path with a genuine peer
    (this test process itself), so V1's SO_PEERCRED read is exercised for
    real — no root or systemd socket activation needed since any process
    can read its own peer credentials on a UNIX socket."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-gate-sock-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _roundtrip(self, server, request, cgroup_text):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            thread = threading.Thread(
                target=gate_server.serve_one, args=(server, b),
                kwargs={"cgroup_reader": lambda pid: cgroup_text})
            thread.start()
            a.sendall(json.dumps(request).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = a.recv(65536)
                if not chunk:
                    break
                buf += chunk
            thread.join(timeout=5)
            return json.loads(buf.decode())
        finally:
            a.close()
            b.close()

    def test_submit_over_a_real_socket(self):
        server = make_server(self.tmp)
        reply = self._roundtrip(server, {"verb": "submit", "manifest": good_manifest()},
                                 WORKER_CGROUP)
        self.assertTrue(reply["ok"])
        self.assertIn("req", reply)

    def test_unknown_verb_over_a_real_socket(self):
        server = make_server(self.tmp)
        reply = self._roundtrip(server, {"verb": "nonsense"}, WORKER_CGROUP)
        self.assertFalse(reply["ok"])

    def test_refusal_carries_its_code_over_the_wire(self):
        server = make_server(self.tmp)
        reply = self._roundtrip(server, {"verb": "submit", "manifest": good_manifest()},
                                 APPROVER_CGROUP)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["code"], "OPS_IDENTITY")


if __name__ == "__main__":
    unittest.main()
