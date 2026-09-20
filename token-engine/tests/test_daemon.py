#!/usr/bin/env python3
"""Unit tests for the token-engine daemon's pure logic (wq#2270).

Runs as any uid, no socket, no root: grant handling, verb dispatch, response
schema assertion, mask tripwire patterns. The installed-system half of the gate
(EACCES, unit active, live audit lines) is replayed by install.sh and the
evidence commands, not simulated here.

    python3 token-engine/tests/test_daemon.py
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, "/home/lumen/bin")  # fleet_exec_policy, as installed
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import token_engine_daemon as D


def write_grant(tmpdir, ticket="wq2270", kind="token_engine", node="daemon",
                expires="2099-01-01T00:00:00Z"):
    grant = {"ticket": ticket, "expires": expires,
             "grants": [{"kind": kind, "node": node}]}
    raw = json.dumps(grant).encode()
    path = os.path.join(tmpdir, "grant.json")
    with open(path, "wb") as f:
        f.write(raw)
    return path, hashlib.sha256(raw).hexdigest()


def req(path, sha, verb="ping", ticket="wq2270", worker="w1"):
    return {"verb": verb, "argv": [], "ticket": ticket, "attempt": 1,
            "worker": worker, "grant_file": path, "grant_sha": sha}


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path, self.sha = write_grant(self.tmp.name)
        self.peer = (1234, 1000, 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ping_allowed_with_valid_grant(self):
        ok, rule, _, result, decision = D.handle(req(self.path, self.sha), self.peer)
        self.assertTrue(ok)
        self.assertEqual(decision, "allow")
        self.assertTrue(result["pong"])

    def test_unknown_verb_denied(self):
        ok, rule, *_ , decision = D.handle(req(self.path, self.sha, verb="rm-rf"), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("verb_unknown", "deny"))

    def test_sha_mismatch_denied(self):
        ok, rule, *_, decision = D.handle(req(self.path, "0" * 64), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("grant:grant_sha_mismatch", "deny"))

    def test_ticket_mismatch_denied(self):
        ok, rule, *_, decision = D.handle(req(self.path, self.sha, ticket="wq9999"), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("grant:grant_ticket_mismatch", "deny"))

    def test_expired_grant_denied(self):
        path, sha = write_grant(self.tmp.name, expires="2020-01-01T00:00:00Z")
        ok, rule, *_, decision = D.handle(req(path, sha), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("grant:grant_expired", "deny"))

    def test_missing_grant_env_denied(self):
        ok, rule, *_, decision = D.handle(req(None, None), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("grant:grant_missing_env", "deny"))

    def test_wrong_kind_denied(self):
        path, sha = write_grant(self.tmp.name, kind="git_read", node="repo:x/y")
        ok, rule, *_, decision = D.handle(req(path, sha), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("grant_no_match", "deny"))

    def test_malformed_worker_denied(self):
        ok, rule, *_, decision = D.handle(req(self.path, self.sha, worker="../../etc"), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("worker_malformed", "deny"))


class FrameTest(unittest.TestCase):
    def test_schema_accepts_documented_frame(self):
        D.assert_response_schema({"ok": True, "verb": "ping", "ticket": "wq2270",
                                  "rule": "r", "reason": "ok", "result": {"pong": True},
                                  "elapsed_s": 0.01})

    def test_schema_rejects_extra_key(self):
        with self.assertRaises(ValueError):
            D.assert_response_schema({"ok": True, "verb": "p", "ticket": "t",
                                      "rule": "r", "reason": "", "result": {},
                                      "elapsed_s": 0, "token": "x"})

    def test_schema_rejects_nested_result(self):
        with self.assertRaises(ValueError):
            D.assert_response_schema({"ok": True, "verb": "p", "ticket": "t",
                                      "rule": "r", "reason": "", "result": {"a": {"b": 1}},
                                      "elapsed_s": 0})

    def test_mask_catches_selftest_and_provider_shapes(self):
        leak = D.verb_mask_selftest({}, {})
        self.assertEqual(D.mask_hits(json.dumps(leak)), ["github_token"])
        self.assertIn("aws_key_id", D.mask_hits("AKIA" + "A" * 16))
        self.assertIn("kv_secret", D.mask_hits('TOKEN="abcdefgh12345678"'))
        self.assertEqual(D.mask_hits('{"pong": true, "daemon_uid": 998}'), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
