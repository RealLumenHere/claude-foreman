import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ops_apply import queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-queue-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stage_then_load_round_trips(self):
        manifest = {"recipe": "x", "ticket": 4764}
        req = queue.stage(self.tmp, 4764, manifest, "sha256:abc")
        record = queue.load(self.tmp, 4764, req)
        self.assertEqual(record["state"], queue.STATE_STAGED)
        self.assertEqual(record["manifest"], manifest)
        self.assertEqual(record["digest"], "sha256:abc")
        self.assertIsNone(record["consumed_at"])

    def test_load_missing_is_none(self):
        self.assertIsNone(queue.load(self.tmp, 1, "nope"))

    def test_approve_requires_staged(self):
        manifest = {"recipe": "x", "ticket": 1}
        req = queue.stage(self.tmp, 1, manifest, "sha256:abc")
        queue.approve(self.tmp, 1, req, approver_cgroup_text="orchestrator", now=1000)
        with self.assertRaises(ValueError):
            queue.approve(self.tmp, 1, req, approver_cgroup_text="orchestrator", now=1000)

    def test_consume_is_one_shot(self):
        manifest = {"recipe": "x", "ticket": 1}
        req = queue.stage(self.tmp, 1, manifest, "sha256:abc")
        queue.approve(self.tmp, 1, req, approver_cgroup_text="orchestrator", now=1000)
        queue.consume(self.tmp, 1, req, "sha256:abc", now=1001)
        with self.assertRaises(ValueError):
            queue.consume(self.tmp, 1, req, "sha256:abc", now=1002)

    def test_consume_rejects_digest_mismatch(self):
        manifest = {"recipe": "x", "ticket": 1}
        req = queue.stage(self.tmp, 1, manifest, "sha256:abc")
        queue.approve(self.tmp, 1, req, approver_cgroup_text="orchestrator", now=1000)
        with self.assertRaises(ValueError):
            queue.consume(self.tmp, 1, req, "sha256:different", now=1001)

    def test_consume_rejects_expired(self):
        manifest = {"recipe": "x", "ticket": 1}
        req = queue.stage(self.tmp, 1, manifest, "sha256:abc")
        queue.approve(self.tmp, 1, req, approver_cgroup_text="orchestrator", now=1000, ttl=10)
        with self.assertRaises(ValueError):
            queue.consume(self.tmp, 1, req, "sha256:abc", now=1500)

    def test_pending_applying_finds_only_applying_state(self):
        m = {"recipe": "x", "ticket": 1}
        req1 = queue.stage(self.tmp, 1, m, "sha256:a")
        queue.set_state(self.tmp, 1, req1, queue.STATE_APPLYING, config_path="/tmp/x")
        req2 = queue.stage(self.tmp, 1, m, "sha256:b")
        queue.set_state(self.tmp, 1, req2, queue.STATE_APPLIED)
        pending = queue.pending_applying(self.tmp)
        self.assertEqual([r["req"] for r in pending], [req1])


if __name__ == "__main__":
    unittest.main()
