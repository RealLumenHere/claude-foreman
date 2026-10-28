import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from ops_apply.errors import OpsApplyError
from ops_apply.recipes import cloudflared_ingress_upsert as recipe

BASE_DOC = {
    "tunnel": "abc123",
    "credentials-file": "/etc/cloudflared/abc123.json",
    "ingress": [
        {"hostname": "existing.lumennode.uk", "service": "http://127.0.0.1:9000"},
        {"service": "http_status:404"},
    ],
}


def _doc(ingress_body):
    return {**{k: v for k, v in BASE_DOC.items() if k != "ingress"},
            "ingress": ingress_body + [recipe.CATCHALL]}


class ApplyIngressOpTests(unittest.TestCase):
    def test_upsert_new_host(self):
        new_body, inverse = recipe.apply_ingress_op(
            BASE_DOC["ingress"], {"action": "upsert", "hostname": "new.lumennode.uk", "port": 8123})
        self.assertIn({"hostname": "new.lumennode.uk", "service": "http://127.0.0.1:8123"}, new_body)
        self.assertEqual(inverse, {"action": "remove", "hostname": "new.lumennode.uk", "port": 8123})

    def test_upsert_existing_host_changes_port(self):
        new_body, inverse = recipe.apply_ingress_op(
            BASE_DOC["ingress"], {"action": "upsert", "hostname": "existing.lumennode.uk", "port": 9100})
        self.assertIn({"hostname": "existing.lumennode.uk", "service": "http://127.0.0.1:9100"}, new_body)
        self.assertEqual(inverse, {"action": "upsert", "hostname": "existing.lumennode.uk", "port": 9000})

    def test_remove_existing_host(self):
        new_body, inverse = recipe.apply_ingress_op(
            BASE_DOC["ingress"], {"action": "remove", "hostname": "existing.lumennode.uk"})
        self.assertNotIn("existing.lumennode.uk", [r.get("hostname") for r in new_body])
        self.assertEqual(inverse, {"action": "upsert", "hostname": "existing.lumennode.uk", "port": 9000})

    def test_remove_missing_host_refuses(self):
        with self.assertRaises(recipe.RecipeError):
            recipe.apply_ingress_op(BASE_DOC["ingress"], {"action": "remove", "hostname": "ghost.lumennode.uk"})


class ValidateDiffTests(unittest.TestCase):
    def test_single_add_passes(self):
        new_body, _ = recipe.apply_ingress_op(
            BASE_DOC["ingress"], {"action": "upsert", "hostname": "new.lumennode.uk", "port": 8123})
        recipe.validate_diff(BASE_DOC, _doc(new_body))

    def test_other_key_changed_refuses(self):
        new_doc = dict(BASE_DOC)
        new_doc["tunnel"] = "different"
        with self.assertRaises(recipe.RecipeError) as ctx:
            recipe.validate_diff(BASE_DOC, new_doc)
        self.assertIn("other_key_changed", ctx.exception.reason)

    def test_catchall_removed_refuses(self):
        new_doc = {**BASE_DOC, "ingress": [BASE_DOC["ingress"][0]]}
        with self.assertRaises(recipe.RecipeError) as ctx:
            recipe.validate_diff(BASE_DOC, new_doc)
        self.assertIn("catchall", ctx.exception.reason)

    def test_duplicate_host_path_refuses(self):
        dup = [{"hostname": "existing.lumennode.uk", "service": "http://127.0.0.1:9000"},
               {"hostname": "existing.lumennode.uk", "service": "http://127.0.0.1:9100"}]
        with self.assertRaises(recipe.RecipeError) as ctx:
            recipe.validate_diff(BASE_DOC, _doc(dup))
        self.assertIn("duplicate_host_path", ctx.exception.reason)

    def test_non_loopback_service_refuses(self):
        bad = BASE_DOC["ingress"][:-1] + [{"hostname": "new.lumennode.uk", "service": "http://evil.example/"}]
        with self.assertRaises(recipe.RecipeError) as ctx:
            recipe.validate_diff(BASE_DOC, _doc(bad))
        self.assertIn("non_loopback_service", ctx.exception.reason)

    def test_multiple_rules_changed_refuses(self):
        changed = [{"hostname": "existing.lumennode.uk", "service": "http://127.0.0.1:1111"},
                   {"hostname": "new.lumennode.uk", "service": "http://127.0.0.1:2222"}]
        with self.assertRaises(recipe.RecipeError) as ctx:
            recipe.validate_diff(BASE_DOC, _doc(changed))
        self.assertIn("expected_exactly_one_rule_changed", ctx.exception.reason)


class FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class FakeRunner:
    """Replays canned results keyed by argv[0:2]; everything else returns
    success with empty stdout."""

    def __init__(self, overrides=None, record_restarts=True):
        self.overrides = overrides or {}
        self.calls = []
        self.record_restarts = record_restarts

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        key = tuple(argv[:2])
        if key in self.overrides:
            return self.overrides[key]
        if argv[:2] == ["systemctl", "is-active"]:
            return FakeCompleted(0, "active\n")
        return FakeCompleted(0, "")


class ApplyEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="opsapply-recipe-")
        self.config_path = os.path.join(self.tmp, "config.yml")
        with open(self.config_path, "w") as f:
            yaml.safe_dump(BASE_DOC, f, sort_keys=False)
        self.work_dir = os.path.join(self.tmp, "work")
        self.backup_root = os.path.join(self.tmp, "backups")
        self.manifest = {
            "recipe": "cloudflared.ingress.upsert", "recipe_version": 1, "node": "bes",
            "ticket": 4764, "reason": "test",
            "params": {"action": "upsert", "hostname": "new.lumennode.uk", "port": 8123},
        }

    def _apply(self, runner, http_get=None, req_id="req-1"):
        return recipe.apply(manifest=self.manifest, work_dir=self.work_dir,
                            backup_root=self.backup_root, req_id=req_id,
                            runner=runner, http_get=http_get, config_path=self.config_path)

    def test_successful_apply_writes_new_rule_and_backs_up_old(self):
        result = self._apply(FakeRunner(), http_get=lambda url: 200)
        self.assertEqual(result.status, "APPLIED")
        with open(self.config_path) as f:
            live = yaml.safe_load(f)
        self.assertIn({"hostname": "new.lumennode.uk", "service": "http://127.0.0.1:8123"},
                      live["ingress"])
        with open(result.backup_path) as f:
            backed_up = yaml.safe_load(f)
        self.assertEqual(backed_up, BASE_DOC)
        self.assertEqual(result.inverse_manifest["params"],
                         {"action": "remove", "hostname": "new.lumennode.uk", "port": 8123})

    def test_forced_post_check_failure_rolls_back(self):
        # cloudflared's own candidate validate/rule still pass at step 4
        # (checked against the as-yet-unapplied candidate) but the post-
        # check's rule probe against the now-live file fails — forcing
        # step 10's rollback path. The probe is keyed on the LIVE file's
        # actual content (not a blind argv string match), so it stops
        # failing once rollback has restored the old file, exactly like a
        # real transient post-apply failure would.
        runner = FakeRunner()

        def flaky_runner(argv, **kwargs):
            if (argv[:2] == ["cloudflared", "tunnel"] and argv[-2:-1] == ["rule"]
                    and len(argv) > 3 and argv[3] == self.config_path):
                with open(self.config_path) as f:
                    if "new.lumennode.uk" in f.read():
                        return FakeCompleted(1, "")
            return runner(argv, **kwargs)

        def http_get(url):
            # expectation after rollback is "remove" (brand-new host never
            # existed before), which wants a 404 — keep it state-aware too
            # so the rollback's own post-check actually gets to pass.
            with open(self.config_path) as f:
                return 200 if "new.lumennode.uk" in f.read() else 404

        result = self._apply(flaky_runner, http_get=http_get)
        self.assertEqual(result.status, "ROLLED_BACK")
        with open(self.config_path) as f:
            live = yaml.safe_load(f)
        self.assertEqual(live, BASE_DOC)
        # the backup is retained, not consumed, for audit/revert
        self.assertTrue(os.path.exists(os.path.join(self.backup_root, "req-1", "config.yml")))

    def test_cloudflare_error_status_triggers_rollback(self):
        # 1033 only while the bad change is live; a real Cloudflare edge
        # would stop erroring once the old config is restored (and the
        # rollback's own post-check expects 404 for a brand-new host that
        # never existed before), so the stub is keyed on the live file's
        # content, not a constant.
        def http_get(url):
            with open(self.config_path) as f:
                return 1033 if "new.lumennode.uk" in f.read() else 404

        result = self._apply(FakeRunner(), http_get=http_get)
        self.assertEqual(result.status, "ROLLED_BACK")
        with open(self.config_path) as f:
            self.assertEqual(yaml.safe_load(f), BASE_DOC)

    def test_post_check_timeout_triggers_rollback(self):
        # deadline = clock() [at apply()'s step-9 call] + 60; a clock that
        # jumps far past that by the post-check's first probe forces a
        # timeout, exactly like a transient delay in the real post-check
        # window would.
        clock_calls = {"n": 0}

        def slow_clock():
            clock_calls["n"] += 1
            return 0.0 if clock_calls["n"] == 1 else 1000.0

        def http_get(url):
            with open(self.config_path) as f:
                return 200 if "new.lumennode.uk" in f.read() else 404

        result = recipe.apply(manifest=self.manifest, work_dir=self.work_dir,
                              backup_root=self.backup_root, req_id="req-timeout",
                              runner=FakeRunner(), clock=slow_clock, http_get=http_get,
                              config_path=self.config_path, post_check_timeout=60)
        self.assertEqual(result.status, "ROLLED_BACK")
        self.assertIn("post_check_timeout", result.detail)

    def test_rollback_restart_failure_is_rollback_failed_not_an_exception(self):
        calls = {"n": 0}

        def runner(argv, **kwargs):
            if argv[:2] == ["systemctl", "restart"]:
                calls["n"] += 1
                # first restart (step 8) succeeds; the rollback's restart fails
                return FakeCompleted(0 if calls["n"] == 1 else 1, "")
            if argv[:2] == ["systemctl", "is-active"]:
                return FakeCompleted(0, "active\n")
            return FakeCompleted(0, "")

        result = self._apply(runner, http_get=lambda url: 1033)
        self.assertEqual(result.status, "ROLLBACK_FAILED")
        self.assertIn("restart_failed_on_rollback", result.detail)

    def test_remove_action_post_check_expects_404(self):
        self.manifest["params"] = {"action": "remove", "hostname": "existing.lumennode.uk"}
        result = self._apply(FakeRunner(), http_get=lambda url: 404)
        self.assertEqual(result.status, "APPLIED")

    def test_cloudflared_validate_failure_refuses_before_any_write(self):
        def runner(argv, **kwargs):
            if argv[-1] == "validate":
                return FakeCompleted(1, "")
            return FakeCompleted(0, "")

        with self.assertRaises(recipe.RecipeError):
            self._apply(runner)
        with open(self.config_path) as f:
            self.assertEqual(yaml.safe_load(f), BASE_DOC)
        self.assertFalse(os.path.isdir(self.backup_root))

    def test_toctou_detected_when_live_file_changes_after_read(self):
        def runner(argv, **kwargs):
            if argv[-1] == "validate":
                # simulate a racing writer mutating the live file right
                # after step 1's read, before step 6's recheck.
                with open(self.config_path, "a") as f:
                    f.write("# raced\n")
            return FakeCompleted(0, "")

        with self.assertRaises(OpsApplyError) as ctx:
            self._apply(runner)
        self.assertEqual(ctx.exception.code, "OPS_TOCTOU")


if __name__ == "__main__":
    unittest.main()
