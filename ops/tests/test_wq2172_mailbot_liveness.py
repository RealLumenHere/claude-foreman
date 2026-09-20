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


if __name__ == "__main__":
    unittest.main()
