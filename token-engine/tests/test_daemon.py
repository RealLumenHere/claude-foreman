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
        leak = D.verb_mask_selftest({}, {}, None)
        self.assertEqual(D.mask_hits(json.dumps(leak)), ["github_token"])
        self.assertIn("aws_key_id", D.mask_hits("AKIA" + "A" * 16))
        self.assertIn("kv_secret", D.mask_hits('TOKEN="abcdefgh12345678"'))
        self.assertEqual(D.mask_hits('{"pong": true, "daemon_uid": 998}'), [])


POLICY_YAML = """\
never: []
gateway_admin_ops: [mint, revoke]
api_providers: [cloudflare, ebay]
plug_names: [testplug]
run_readonly_cmds: [uptime, hostname]
"""


class T10VerbTest(unittest.TestCase):
    """wq#2278: gateway-key / api-call / plug / ssh-exec — every response is
    schema-flat, mask-clean, and the credential never leaves via a frame."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = {k: getattr(D, k) for k in
                      ("SECRETS_DIR", "HANDOFF_DIR", "POLICY_PATH", "PLUG_CTL",
                       "http_call")}
        D.SECRETS_DIR = os.path.join(root, "state")
        D.HANDOFF_DIR = os.path.join(root, "handoff")
        D.POLICY_PATH = os.path.join(root, "policy.yaml")
        os.makedirs(os.path.join(D.SECRETS_DIR, "secrets", "fleet-ssh"))
        os.makedirs(D.HANDOFF_DIR)
        with open(D.POLICY_PATH, "w") as f:
            f.write(POLICY_YAML)
        self.peer = (1234, 1000, 1000)
        self.http_calls = []

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(D, k, v)
        self.tmp.cleanup()

    def write_secret(self, name, value):
        path = os.path.join(D.SECRETS_DIR, "secrets", name)
        with open(path, "w") as f:
            f.write(value)

    def grant_req(self, kind, node, verb, argv, ticket="wq2278"):
        path, sha = write_grant(self.tmp.name, ticket=ticket, kind=kind, node=node)
        r = req(path, sha, verb=verb, ticket=ticket)
        r["argv"] = argv
        return r

    def fake_http(self, responses):
        def http(method, url, headers, body=None, timeout=30):
            self.http_calls.append((method, url, headers, body))
            return responses.pop(0)
        D.http_call = http

    # ---- gateway-key ----

    SECRET = "sk-litellm-minted-abcdef1234567890"

    def mint(self):
        self.write_secret("litellm-admin.token", "sk-admin-000000000000000000")
        self.fake_http([(200, json.dumps(
            {"key": self.SECRET, "expires": "2026-09-21T00:00:00Z"}))])
        return D.handle(self.grant_req("gateway_admin", "gateway:litellm",
                                       "gateway-key", ["mint", "24h", "15"]),
                        self.peer)

    def test_gateway_mint_returns_id_and_expiry_never_the_key(self):
        ok, rule, _, result, decision = self.mint()
        self.assertTrue(ok, rule)
        self.assertEqual(set(result), {"key_id", "expires", "key_file"})
        frame_text = json.dumps(result, sort_keys=True)
        self.assertNotIn(self.SECRET, frame_text)
        self.assertEqual(D.mask_hits(frame_text), [])
        with open(result["key_file"]) as f:
            self.assertEqual(f.read(), self.SECRET)
        # the mint request itself carried the admin bearer — but only outbound
        # to the gateway, never into the result
        self.assertIn("Authorization", self.http_calls[0][2])

    def test_gateway_mint_bad_ttl_denied(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("gateway_admin", "gateway:litellm", "gateway-key",
                           ["mint", "eternal"]), self.peer)
        self.assertEqual((ok, rule, decision),
                         (False, "gateway_ttl_malformed", "deny"))

    def test_gateway_op_not_in_policy_denied(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("gateway_admin", "gateway:litellm", "gateway-key",
                           ["rotate"]), self.peer)
        self.assertEqual((ok, rule, decision),
                         (False, "gateway_op_not_allowed", "deny"))

    def test_gateway_revoke_deletes_record_and_is_idempotent(self):
        self.mint()
        self.fake_http([(200, "{}")])
        ok, rule, _, result, _ = D.handle(
            self.grant_req("gateway_admin", "gateway:litellm", "gateway-key",
                           ["revoke"]), self.peer)
        self.assertTrue(ok, rule)
        self.assertTrue(result["revoked"])
        self.assertFalse(os.path.exists(D.handoff_path("wq2278")))
        self.assertIn(self.SECRET, str(self.http_calls[-1][3]))  # /key/delete body
        ok, _, _, result, _ = D.handle(
            self.grant_req("gateway_admin", "gateway:litellm", "gateway-key",
                           ["revoke"]), self.peer)
        self.assertTrue(ok)
        self.assertFalse(result["revoked"])

    def test_gateway_wrong_grant_kind_denied(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("plug", "plug:testplug", "gateway-key", ["mint"]),
            self.peer)
        self.assertEqual((ok, rule, decision), (False, "grant_no_match", "deny"))

    # ---- api-call ----

    def test_api_call_result_is_status_and_body_only(self):
        self.write_secret("cloudflare-api.token", "cf-admin-token-value-000")
        self.fake_http([(200, '{"success": true}')])
        ok, rule, _, result, _ = D.handle(
            self.grant_req("api:cloudflare", "api:cloudflare", "api-call",
                           ["cloudflare", "GET", "/client/v4/user/tokens/verify"]),
            self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual(set(result), {"status", "body"})
        self.assertNotIn("cf-admin-token-value-000", json.dumps(result))
        # the Authorization header went out to the provider only
        self.assertEqual(self.http_calls[0][2]["Authorization"],
                         "Bearer cf-admin-token-value-000")

    def test_api_call_unlisted_provider_denied(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("api:github", "api:github", "api-call",
                           ["github", "GET", "/user"]), self.peer)
        self.assertEqual((ok, rule, decision),
                         (False, "api_provider_not_allowed", "deny"))

    def test_api_call_body_via_relative_path_denied(self):
        self.write_secret("cloudflare-api.token", "x" * 24)
        ok, rule, *_, decision = D.handle(
            self.grant_req("api:cloudflare", "api:cloudflare", "api-call",
                           ["cloudflare", "POST", "/v4/zones", "body.json"]),
            self.peer)
        self.assertEqual((ok, rule, decision),
                         (False, "body_file_relative", "deny"))

    # ---- plug ----

    def test_plug_runs_ctl_and_returns_state(self):
        D.PLUG_CTL = "/bin/echo"
        ok, rule, _, result, _ = D.handle(
            self.grant_req("plug", "plug:testplug", "plug",
                           ["testplug", "state"]), self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("testplug state", result["output"])

    def test_plug_unlisted_name_denied(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("plug", "plug:heater", "plug", ["heater", "on"]),
            self.peer)
        self.assertEqual((ok, rule, decision), (False, "plug_not_allowed", "deny"))

    def test_plug_bad_op_denied(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("plug", "plug:testplug", "plug",
                           ["testplug", "toggle"]), self.peer)
        self.assertEqual((ok, rule, decision), (False, "plug_argv", "deny"))

    # ---- ssh-exec ----

    def test_ssh_exec_requires_ssh_grant(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("plug", "plug:testplug", "ssh-exec",
                           ["thor", "uptime"]), self.peer)
        self.assertEqual((ok, rule, decision), (False, "grant_no_match", "deny"))

    def test_ssh_exec_command_off_allowlist_denied(self):
        ok, rule, *_, decision = D.handle(
            self.grant_req("ssh", "thor", "ssh-exec", ["thor", "rm", "-rf", "/x"]),
            self.peer)
        self.assertFalse(ok)
        self.assertEqual(decision, "deny")

    def test_ssh_exec_key_lives_in_the_store(self):
        # allowlisted cmd + ssh grant, but no key migrated yet: the deny names
        # the store path, proving the daemon never reaches for ~lumen/.ssh
        ok, rule, _, reason_result, decision = D.handle(
            self.grant_req("ssh", "thor", "ssh-exec", ["thor", "uptime"]),
            self.peer)
        self.assertEqual((ok, rule), (False, "ssh_key_missing"))
        key = D.secret_path("fleet-ssh", "id_ed25519")
        self.assertTrue(key.startswith(D.SECRETS_DIR))

    def test_policy_unreadable_fails_closed(self):
        os.unlink(D.POLICY_PATH)
        ok, rule, *_, decision = D.handle(
            self.grant_req("plug", "plug:testplug", "plug",
                           ["testplug", "state"]), self.peer)
        self.assertEqual((ok, rule, decision), (False, "policy_unreadable", "deny"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
