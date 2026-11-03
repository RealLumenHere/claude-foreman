"""wq#4829 CAS: blobs stored by sha256 digest, verified by re-hash at apply
time, never modified in place. Unit tests drive cas.py directly; the
integration tests drive GateServer/NodeServer end-to-end with an
artifact-bearing manifest, including the TOCTOU case the CAS exists to
close: tamper with the stored blob after approval and the apply is refused."""
import os
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from ops_apply import cas, gate_server, node_server
from ops_apply.canonical import manifest_digest, sha256_hex
from ops_apply.errors import OpsApplyError


class CasBlobTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="opsapply-cas-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _tamper(self, digest, content=b"evil"):
        path = cas.blob_path(self.root, digest)
        os.chmod(path, 0o644)
        with open(path, "wb") as f:
            f.write(content)

    def test_store_places_blob_at_prefix_path_read_only(self):
        digest = cas.store(self.root, b"echo hi\n")
        self.assertEqual(digest, "sha256:" + sha256_hex(b"echo hi\n"))
        path = cas.blob_path(self.root, digest)
        hexpart = digest.split(":")[1]
        self.assertEqual(path, os.path.join(self.root, hexpart[:2], hexpart))
        self.assertTrue(os.path.exists(path))
        self.assertFalse(stat.S_IMODE(os.stat(path).st_mode) & 0o222)

    def test_store_is_idempotent_dedupe(self):
        d1 = cas.store(self.root, b"same bytes")
        d2 = cas.store(self.root, "same bytes")
        self.assertEqual(d1, d2)

    def test_verify_returns_path_for_intact_blob(self):
        digest = cas.store(self.root, b"playbook: x\n")
        self.assertEqual(cas.verify(self.root, digest), cas.blob_path(self.root, digest))

    def test_verify_refuses_tampered_blob(self):
        digest = cas.store(self.root, b"original")
        self._tamper(digest)
        with self.assertRaises(OpsApplyError) as ctx:
            cas.verify(self.root, digest)
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_verify_refuses_missing_blob(self):
        with self.assertRaises(OpsApplyError) as ctx:
            cas.verify(self.root, "sha256:" + "0" * 64)
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_verify_refuses_malformed_digest(self):
        for bad in ("md5:" + "0" * 64, "sha256:short", "sha256:" + "g" * 64, None):
            with self.assertRaises(OpsApplyError):
                cas.verify(self.root, bad)

    def test_retrieve_rehashes_on_every_read(self):
        digest = cas.store(self.root, b"diff --git a b\n")
        self.assertEqual(cas.retrieve(self.root, digest), b"diff --git a b\n")
        self._tamper(digest)
        with self.assertRaises(OpsApplyError):
            cas.retrieve(self.root, digest)


class CasManifestTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="opsapply-cas-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_record_and_load_round_trip(self):
        d = cas.store(self.root, b"a")
        cas.record_artifact(self.root, 4829, "install.sh", d, req="r1", now=1.0)
        doc = cas.load_ticket_manifest(self.root, 4829)
        self.assertEqual(doc["ticket"], 4829)
        self.assertEqual(doc["artifacts"][0]["name"], "install.sh")
        self.assertEqual(doc["artifacts"][0]["digest"], d)

    def test_restaging_same_pair_updates_instead_of_duplicating(self):
        d = cas.store(self.root, b"a")
        cas.record_artifact(self.root, 4829, "install.sh", d, now=1.0)
        cas.record_artifact(self.root, 4829, "install.sh", d, now=2.0)
        doc = cas.load_ticket_manifest(self.root, 4829)
        self.assertEqual(len(doc["artifacts"]), 1)
        self.assertEqual(doc["artifacts"][0]["stored_at"], 2.0)

    def test_record_approval_stamps_matching_digest(self):
        d = cas.store(self.root, b"a")
        cas.record_artifact(self.root, 4829, "install.sh", d)
        cas.record_approval(self.root, 4829, d, {"approved_by": "orch", "approved_at": 3.0})
        doc = cas.load_ticket_manifest(self.root, 4829)
        self.assertEqual(doc["artifacts"][0]["approval"]["approved_by"], "orch")

    def test_manifest_lives_under_manifests_dir(self):
        d = cas.store(self.root, b"a")
        cas.record_artifact(self.root, 4829, "x", d)
        self.assertTrue(os.path.exists(os.path.join(self.root, "manifests", "wq4829.json")))


# ---------------------------------------------------------------------------
# gate + node integration (fixtures mirror test_gate_server/test_node_server)
# ---------------------------------------------------------------------------

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
LEASE = {"status": "claimed", "lane": "ops-apply", "lease_owner": WORKER_UNIT,
         "lease_expires_at": 10 ** 12}

ARTIFACT_BYTES = b"#!/bin/sh\necho staged install step\n"
ARTIFACT_DIGEST = "sha256:" + sha256_hex(ARTIFACT_BYTES)


def artifact_manifest(**overrides):
    m = {"recipe": "cloudflared.ingress.upsert", "recipe_version": 1, "node": "bes",
         "ticket": 4764, "params": {"action": "upsert", "hostname": "x.lumennode.uk", "port": 8123},
         "reason": "t", "artifacts": {"install.sh": ARTIFACT_DIGEST}}
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


class GateCasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-gatecas-")
        self.cas_root = os.path.join(self.tmp, "cas")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _server(self, cas_root=True, node_transport=None):
        return gate_server.GateServer(
            catalog=CATALOG, policy=POLICY, policy_sha_actual="p" * 64,
            queue_dir=os.path.join(self.tmp, "queue"),
            lease_lookup=lambda ticket: LEASE,
            node_transport=node_transport or (lambda env: {"state": "APPLIED"}),
            sign_fn=lambda digest: "fake-signature",
            recipe_file_sha256="r" * 64, pinned_hostname="bes", pinned_machine_id="m1",
            reported_machine_id="m1", never_nodes=[],
            cas_root=self.cas_root if cas_root else None)

    def test_submit_stores_artifact_and_records_ticket_manifest(self):
        server = self._server()
        result = server.handle_submit(artifact_manifest(), WORKER_CGROUP,
                                       artifact_content={"install.sh": ARTIFACT_BYTES.decode()})
        self.assertIn("req", result)
        self.assertEqual(cas.retrieve(self.cas_root, ARTIFACT_DIGEST), ARTIFACT_BYTES)
        doc = cas.load_ticket_manifest(self.cas_root, 4764)
        self.assertEqual(doc["artifacts"][0]["digest"], ARTIFACT_DIGEST)

    def test_submit_refuses_content_not_matching_declared_digest(self):
        server = self._server()
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_submit(artifact_manifest(), WORKER_CGROUP,
                                  artifact_content={"install.sh": "something else"})
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_submit_refuses_declared_artifact_with_no_content_or_blob(self):
        server = self._server()
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_submit(artifact_manifest(), WORKER_CGROUP)
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_submit_dedupe_accepts_already_stored_blob_without_content(self):
        cas.store(self.cas_root, ARTIFACT_BYTES)
        server = self._server()
        result = server.handle_submit(artifact_manifest(), WORKER_CGROUP)
        self.assertIn("req", result)

    def test_submit_refuses_artifacts_when_gate_has_no_cas_root(self):
        server = self._server(cas_root=False)
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_submit(artifact_manifest(), WORKER_CGROUP,
                                  artifact_content={"install.sh": ARTIFACT_BYTES.decode()})
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_approve_refuses_blob_tampered_after_submit(self):
        """The TOCTOU the CAS closes on the gate side: approve re-reads the
        artifact through a verified read, so bytes swapped after submit are
        refused instead of forwarded."""
        server = self._server()
        req = server.handle_submit(artifact_manifest(), WORKER_CGROUP,
                                    artifact_content={"install.sh": ARTIFACT_BYTES.decode()})["req"]
        path = cas.blob_path(self.cas_root, ARTIFACT_DIGEST)
        os.chmod(path, 0o644)
        with open(path, "wb") as f:
            f.write(b"evil payload")
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_approve(4764, req, APPROVER_CGROUP)
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_approve_forwards_artifact_content_and_stamps_approval(self):
        seen = {}

        def transport(envelope):
            seen.update(envelope)
            return {"state": "APPLIED"}

        server = self._server(node_transport=transport)
        req = server.handle_submit(artifact_manifest(), WORKER_CGROUP,
                                    artifact_content={"install.sh": ARTIFACT_BYTES.decode()})["req"]
        server.handle_approve(4764, req, APPROVER_CGROUP)
        self.assertEqual(seen["artifact_content"]["install.sh"], ARTIFACT_BYTES.decode())
        doc = cas.load_ticket_manifest(self.cas_root, 4764)
        self.assertEqual(doc["artifacts"][0]["approval"]["req"], req)
        self.assertIn("orchestrator", doc["artifacts"][0]["approval"]["approved_by"])


class NodeCasTests(unittest.TestCase):
    BASE_DOC = {"tunnel": "abc", "ingress": [{"service": "http_status:404"}]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-nodecas-")
        self.cas_root = os.path.join(self.tmp, "cas")
        self.config_path = os.path.join(self.tmp, "config.yml")
        with open(self.config_path, "w") as f:
            yaml.safe_dump(self.BASE_DOC, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _server(self, **overrides):
        kwargs = dict(
            catalog=CATALOG, policy=POLICY, policy_sha_actual="p" * 64,
            queue_dir=os.path.join(self.tmp, "queue"),
            work_dir=os.path.join(self.tmp, "work"),
            backup_root=os.path.join(self.tmp, "backups"),
            verify_fn=lambda **kw: True, allowed_signers_path="/dev/null",
            recipe_file_sha256="r" * 64, pinned_hostname="bes", pinned_machine_id="m1",
            reported_machine_id="m1", never_nodes=[], config_path=self.config_path,
            runner=fake_runner, http_get=lambda url: 200, cas_root=self.cas_root)
        kwargs.update(overrides)
        return node_server.NodeServer(**kwargs)

    def _envelope(self, manifest=None, artifact_content=None):
        manifest = manifest or artifact_manifest()
        env = {"manifest": manifest, "digest": manifest["digest"], "signature": "sig",
               "req": "req-1", "expires_at": time.time() + 3600}
        if artifact_content is not None:
            env["artifact_content"] = artifact_content
        return env

    def test_apply_verifies_and_stores_forwarded_artifact(self):
        server = self._server()
        result = server.handle_apply(
            self._envelope(artifact_content={"install.sh": ARTIFACT_BYTES.decode()}))
        self.assertEqual(result["state"], "APPLIED")
        self.assertEqual(cas.retrieve(self.cas_root, ARTIFACT_DIGEST), ARTIFACT_BYTES)
        doc = cas.load_ticket_manifest(self.cas_root, 4764)
        self.assertEqual(doc["artifacts"][0]["digest"], ARTIFACT_DIGEST)

    def test_apply_refuses_forwarded_content_that_mismatches_digest(self):
        server = self._server()
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_apply(self._envelope(artifact_content={"install.sh": "evil"}))
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_apply_refuses_blob_tampered_in_node_cas(self):
        """The apply-time re-hash: the blob exists in the node CAS (dedupe
        path, no content resent) but was modified in place — the whole
        apply is refused before anything is staged."""
        cas.store(self.cas_root, ARTIFACT_BYTES)
        path = cas.blob_path(self.cas_root, ARTIFACT_DIGEST)
        os.chmod(path, 0o644)
        with open(path, "wb") as f:
            f.write(b"evil payload")
        server = self._server()
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_apply(self._envelope())
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_apply_refuses_artifacts_when_node_has_no_cas_root(self):
        server = self._server(cas_root=None)
        with self.assertRaises(OpsApplyError) as ctx:
            server.handle_apply(
                self._envelope(artifact_content={"install.sh": ARTIFACT_BYTES.decode()}))
        self.assertEqual(ctx.exception.code, "OPS_CAS")

    def test_apply_without_artifacts_is_unchanged(self):
        m = {"recipe": "cloudflared.ingress.upsert", "recipe_version": 1, "node": "bes",
             "ticket": 4764, "params": {"action": "upsert", "hostname": "x.lumennode.uk",
                                          "port": 8123}, "reason": "t"}
        m["digest"] = manifest_digest(m)
        server = self._server(cas_root=None)
        result = server.handle_apply(self._envelope(manifest=m))
        self.assertEqual(result["state"], "APPLIED")


if __name__ == "__main__":
    unittest.main()
