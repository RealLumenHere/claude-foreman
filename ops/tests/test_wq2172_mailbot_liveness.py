#!/usr/bin/env python3
"""wq#2172 — check_live.py judges the deployed mailbot worker against a git revision.

Runs standalone: python3 ops/tests/test_wq2172_mailbot_liveness.py
No Cloudflare access: the wrangler runner is injected, the repo is a throwaway git tree.
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "check_live", Path(__file__).resolve().parents[1] / "wq2172-mailbot-liveness" / "check_live.py"
)
cl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cl)

BANNER = " ⛅️ wrangler 4.106.0\n─────────────\n"


def git(repo, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    if when:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = when
    return subprocess.run(["git", "-C", repo, *args], env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo, path, body, when):
    p = Path(repo, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    git(repo, "add", path)
    git(repo, "commit", "-qm", f"edit {path}", when=when)
    return git(repo, "rev-parse", "HEAD")


def deployments(*created, message=None):
    out = [{"id": f"d{i}", "created_on": c, "versions": [{"version_id": f"v{i}", "percentage": 100}]}
           for i, c in enumerate(created)]
    if message:
        out[-1]["annotations"] = {"workers/message": message}
    return out


class LivenessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        git(self.repo, "init", "-q")
        self.src_sha = commit(self.repo, "src/leakfilter.js", "a", "2026-09-20T16:00:00+00:00")
        # a later commit that is not a deploy input must not move the bar
        commit(self.repo, "evals/note.md", "b", "2026-09-20T18:00:00+00:00")
        self.addCleanup(self.tmp.cleanup)

    def run_main(self, deps, token="x", banner=True, extra=()):
        runner = lambda repo, script: (BANNER if banner else "") + json.dumps(deps)
        env = {"CLOUDFLARE_API_TOKEN": token} if token else {}
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(buf):
            if not token:
                os.environ.pop("CLOUDFLARE_API_TOKEN", None)
            rc = cl.main(["--repo", self.repo, *extra], runner=runner)
        return rc, buf.getvalue()

    def test_deploy_after_commit_is_current(self):
        rc, out = self.run_main(deployments("2026-09-20T16:30:00Z"))
        self.assertEqual(rc, 0, out)
        self.assertIn("CURRENT", out)

    def test_deploy_before_commit_is_stale(self):
        rc, out = self.run_main(deployments("2026-09-20T15:59:59Z"))
        self.assertEqual(rc, 1, out)
        self.assertIn("STALE", out)
        self.assertIn("deploy.sh", out)

    def test_non_deploy_input_commit_does_not_make_stale(self):
        rc, out = self.run_main(deployments("2026-09-20T16:00:01Z"))
        self.assertEqual(rc, 0, out)
        self.assertIn(self.src_sha[:12], out)

    def test_newest_deployment_wins_regardless_of_order(self):
        rc, _ = self.run_main(deployments("2026-09-20T17:00:00Z", "2026-09-19T00:00:00Z"))
        self.assertEqual(rc, 0)

    def test_stamped_message_is_exact_match(self):
        rc, out = self.run_main(deployments("2026-09-20T10:00:00Z", message=f"mailbot {self.src_sha[:9]}"))
        self.assertEqual(rc, 0, out)
        self.assertIn("stamp", out)

    def test_wrong_stamp_falls_back_to_time(self):
        rc, _ = self.run_main(deployments("2026-09-20T10:00:00Z", message="deadbeef1"))
        self.assertEqual(rc, 1)

    def test_head_stamp_past_deploy_input_commit_is_current(self):
        # wq#2214: deploy.sh stamps HEAD, which is a later non-deploy-input commit
        head = git(self.repo, "rev-parse", "--short", "HEAD")
        self.assertNotEqual(head, self.src_sha[:7])
        rc, out = self.run_main(deployments("2026-09-20T10:00:00Z", message=head))
        self.assertEqual(rc, 0, out)
        self.assertIn("at or past", out)

    def test_stamp_of_ancestor_without_deploy_input_commit_is_stale(self):
        commit(self.repo, "src/leakfilter.js", "c", "2026-09-20T19:00:00+00:00")
        # deployed after the new commit, so the time rule alone would call this CURRENT
        rc, out = self.run_main(deployments("2026-09-20T20:00:00Z", message=self.src_sha[:9]))
        self.assertEqual(rc, 1, out)
        self.assertIn("predates", out)

    def test_dirty_stamp_is_not_trusted(self):
        head = git(self.repo, "rev-parse", "--short", "HEAD")
        rc, out = self.run_main(deployments("2026-09-20T10:00:00Z", message=f"{head}-dirty"))
        self.assertEqual(rc, 1, out)
        self.assertNotIn("stamp", out)

    def test_diverged_stamp_falls_back_to_time(self):
        git(self.repo, "checkout", "-q", "-b", "side", self.src_sha)
        side = commit(self.repo, "evals/side.md", "s", "2026-09-20T16:30:00+00:00")
        git(self.repo, "checkout", "-q", "-")
        commit(self.repo, "src/leakfilter.js", "c", "2026-09-20T19:00:00+00:00")
        rc, out = self.run_main(deployments("2026-09-20T20:00:00Z", message=side[:9]))
        self.assertEqual(rc, 0, out)
        self.assertIn("deployed", out)
        rc, _ = self.run_main(deployments("2026-09-20T18:00:00Z", message=side[:9]))
        self.assertEqual(rc, 1)

    def test_no_token_is_unconfirmed_and_never_calls_wrangler(self):
        def boom(*a):
            raise AssertionError("wrangler must not run without a token")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False), contextlib.redirect_stdout(buf):
            os.environ.pop("CLOUDFLARE_API_TOKEN", None)
            rc = cl.main(["--repo", self.repo], runner=boom)
        self.assertEqual(rc, 3)
        self.assertIn("UNCONFIRMED", buf.getvalue())

    def test_empty_deployments_is_unconfirmed(self):
        rc, out = self.run_main([])
        self.assertEqual(rc, 3, out)

    def test_wrangler_failure_is_unconfirmed(self):
        def fail(repo, script):
            raise RuntimeError("wrangler exited 1: auth")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "x"}), contextlib.redirect_stdout(buf):
            rc = cl.main(["--repo", self.repo], runner=fail)
        self.assertEqual(rc, 3)

    def test_secret_change_after_commit_does_not_read_current(self):
        deps = deployments("2026-09-20T15:00:00Z", "2026-09-20T17:00:00Z")
        deps[0]["annotations"] = {"workers/triggered_by": "upload"}
        deps[1]["annotations"] = {"workers/triggered_by": "secret"}
        rc, out = self.run_main(deps)
        self.assertEqual(rc, 1, out)
        self.assertIn("STALE", out)

    def test_only_secret_deployments_is_unconfirmed(self):
        deps = deployments("2026-09-20T17:00:00Z")
        deps[0]["annotations"] = {"workers/triggered_by": "secret"}
        rc, _ = self.run_main(deps)
        self.assertEqual(rc, 3)

    def test_newest_rollback_is_unconfirmed(self):
        deps = deployments("2026-09-20T17:00:00Z")
        deps[0]["annotations"] = {"workers/triggered_by": "rollback"}
        rc, out = self.run_main(deps)
        self.assertEqual(rc, 3, out)
        self.assertIn("rollback", out)

    def test_missing_wrangler_binary_is_unconfirmed_not_a_traceback(self):
        def missing(repo, script):
            raise FileNotFoundError("wrangler")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "x"}), contextlib.redirect_stdout(buf):
            rc = cl.main(["--repo", self.repo], runner=missing)
        self.assertEqual(rc, 3)

    def test_token_value_never_printed(self):
        _, out = self.run_main(deployments("2026-09-20T16:30:00Z"), token="s3cr3t-tok")
        self.assertNotIn("s3cr3t-tok", out)

    def test_banner_free_and_bannered_output_parse_the_same(self):
        d = deployments("2026-09-20T16:30:00Z")
        self.assertEqual(cl.parse_deployments(json.dumps(d, indent=2)), d)
        self.assertEqual(cl.parse_deployments(BANNER + json.dumps(d, indent=2)), d)

    def test_real_worktree_head_resolves(self):
        mailbot = "/home/lumen/mailbot"
        if not os.path.isdir(os.path.join(mailbot, ".git")):
            self.skipTest("no mailbot checkout on this box")
        sha, when = cl.last_deploy_commit(mailbot, "HEAD")
        self.assertEqual(len(sha), 40)
        self.assertIsNotNone(when.tzinfo)


class InstallVerifyTest(unittest.TestCase):
    """ops/VERIFY_CONTRACT.md: exit 0 ok, 1 MISS, 2 could not run; never installs."""
    INSTALL = Path(__file__).resolve().parents[1] / "wq2172-mailbot-liveness" / "install.sh"

    def run_install(self, exit_code, line, *args):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d, "fake_check.py")
            fake.write_text(f"import sys\nprint({line!r})\nprint('second line')\nsys.exit({exit_code})\n")
            env = dict(os.environ, WQ2172_CHECK_LIVE=str(fake), WQ2172_TOKEN_FILE=str(Path(d, "none")))
            p = subprocess.run(["bash", str(self.INSTALL), *args], env=env, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def test_current_is_ok(self):
        rc, out = self.run_install(0, "CURRENT: x", "--verify")
        self.assertEqual(rc, 0, out)
        self.assertIn("VERIFY OK", out)

    def test_stale_is_miss_exit_1(self):
        rc, out = self.run_install(1, "STALE: x", "--verify")
        self.assertEqual(rc, 1, out)
        self.assertIn("MISS STALE: x", out)
        self.assertNotIn("second line", out)

    def test_unconfirmed_is_exit_2_not_a_miss(self):
        rc, out = self.run_install(3, "UNCONFIRMED: no token", "--verify")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("MISS", out)

    def test_without_verify_installs_nothing_and_fails(self):
        rc, out = self.run_install(0, "CURRENT: x")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("CURRENT", out)

    def test_handler_marker_is_visible_to_ops_verified(self):
        head = self.INSTALL.read_text()[:20000]
        self.assertIn('= --verify ]', head)


if __name__ == "__main__":
    unittest.main()
