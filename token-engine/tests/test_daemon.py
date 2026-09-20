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
import subprocess
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


class TicketEndTest(unittest.TestCase):
    """T9 (wq#2277): mint-test → ticket-end through the real handle() path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path, self.sha = write_grant(self.tmp.name)
        self.peer = (1234, 1000, 1000)
        os.environ["TOKEN_ENGINE_STATE_DIR"] = self.tmp.name
        os.environ["TOKEN_ENGINE_ALLOW_TEST_MINT"] = "1"
        # SECRETS_DIR is read at import; re-point it for mint-test's live files.
        self._old_secrets = D.SECRETS_DIR
        D.SECRETS_DIR = self.tmp.name
        D.L.MINTED.clear()

    def tearDown(self):
        D.SECRETS_DIR = self._old_secrets
        del os.environ["TOKEN_ENGINE_STATE_DIR"]
        del os.environ["TOKEN_ENGINE_ALLOW_TEST_MINT"]
        self.tmp.cleanup()

    def test_mint_test_disabled_without_env(self):
        del os.environ["TOKEN_ENGINE_ALLOW_TEST_MINT"]
        ok, rule, *_, decision = D.handle(
            req(self.path, self.sha, verb="mint-test"), self.peer)
        os.environ["TOKEN_ENGINE_ALLOW_TEST_MINT"] = "1"
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("verb_disabled", "deny"))

    def test_mint_then_ticket_end_revokes_and_is_idempotent(self):
        ok, _, _, result, _ = D.handle(
            req(self.path, self.sha, verb="mint-test"), self.peer)
        self.assertTrue(ok)
        live = os.path.join(self.tmp.name, "test-provider",
                            result["token_id"] + ".live")
        self.assertTrue(os.path.exists(live))
        self.assertIn("wq2270", D.L.MINTED)

        ok, rule, _, result, decision = D.handle(
            req(self.path, self.sha, verb="ticket-end"), self.peer)
        self.assertTrue(ok)
        self.assertEqual(decision, "allow")
        self.assertEqual((result["had_entries"], result["revoked"],
                          result["failed"]), (1, 1, 0))
        self.assertFalse(os.path.exists(live))
        self.assertNotIn("wq2270", D.L.MINTED)

        ok, _, _, result, _ = D.handle(
            req(self.path, self.sha, verb="ticket-end"), self.peer)
        self.assertTrue(ok)
        self.assertEqual(result, {"had_entries": 0, "revoked": 0, "failed": 0})

    def test_ticket_end_scoped_to_grant_ticket(self):
        ok, rule, *_, decision = D.handle(
            req(self.path, self.sha, verb="ticket-end", ticket="wq9999"), self.peer)
        self.assertFalse(ok)
        self.assertEqual((rule, decision), ("grant:grant_ticket_mismatch", "deny"))

    def test_mint_response_carries_no_token_value(self):
        ok, _, _, result, _ = D.handle(
            req(self.path, self.sha, verb="mint-test"), self.peer)
        self.assertTrue(ok)
        self.assertEqual(set(result), {"token_id", "provider"})
        self.assertEqual(D.mask_hits(json.dumps(result)), [])


class PrVerbTest(unittest.TestCase):
    """T4 (wq#2273): PR verbs — node-from-argv grant matching, argv shape gate,
    body-file safety, and handler behaviour against a stubbed forge. No network:
    http_json is monkeypatched; the token store is a tempdir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.peer = (1234, 1000, 1000)
        self.path, self.sha = write_grant(
            self.tmp.name, kind="pr_write", node="repo:lumen/lumen-bin")
        self.orig_secrets = D.SECRETS_DIR
        self.orig_http = D.http_json
        self.orig_root = D.BODY_ROOT
        # state dir and body root must be DISJOINT, as in production
        # (/var/lib/token-engine vs /home/lumen) — the state-dir refusal test
        # depends on it.
        self.state = os.path.join(self.tmp.name, "state")
        D.SECRETS_DIR = self.state
        D.BODY_ROOT = self.tmp.name
        os.makedirs(os.path.join(self.state, "secrets"))
        for tok in ("gitea-lumen.token", "github-frank.token"):
            with open(os.path.join(self.state, "secrets", tok), "w") as f:
                f.write("ghp_" + "a" * 20 + "\n")

    def tearDown(self):
        D.SECRETS_DIR = self.orig_secrets
        D.http_json = self.orig_http
        D.BODY_ROOT = self.orig_root
        self.tmp.cleanup()

    def preq(self, verb, argv, kind="pr_write", node="repo:lumen/lumen-bin"):
        path, sha = write_grant(self.tmp.name, kind=kind, node=node)
        r = req(path, sha, verb=verb)
        r["argv"] = argv
        return r

    def body(self, text="pr body\n", name="body.md"):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_node_derived_from_argv_repo(self):
        ok, rule, *_, decision = D.handle(
            self.preq("pr-view", ["other/repo", "7"], kind="pr_read"), self.peer)
        self.assertEqual((ok, rule, decision), (False, "grant_no_match", "deny"))

    def test_malformed_repo_denied(self):
        ok, rule, *_, decision = D.handle(
            self.preq("pr-view", ["/etc/passwd", "7"], kind="pr_read"), self.peer)
        self.assertEqual((ok, rule, decision), (False, "repo_malformed", "deny"))

    def test_argv_metachar_denied(self):
        ok, rule, *_, decision = D.handle(
            self.preq("pr-comment", ["lumen/lumen-bin", "7", "x;rm"],
                      kind="pr_comment"), self.peer)
        self.assertEqual((ok, rule, decision), (False, "argv_metachar", "deny"))

    def test_body_file_in_state_dir_denied(self):
        tok = os.path.join(self.state, "secrets", "gitea-lumen.token")
        ok, rule, *_, decision = D.handle(
            self.preq("pr-comment", ["lumen/lumen-bin", "7", tok],
                      kind="pr_comment"), self.peer)
        self.assertEqual((ok, rule, decision), (False, "body_in_state_dir", "deny"))

    def test_body_file_outside_root_denied(self):
        ok, rule, *_, decision = D.handle(
            self.preq("pr-comment", ["lumen/lumen-bin", "7", "/etc/hostname"],
                      kind="pr_comment"), self.peer)
        self.assertEqual((ok, rule, decision), (False, "body_outside_root", "deny"))

    def test_credential_shaped_body_denied(self):
        # 36-char body, the real ghp_ width: wq#2292 raised the floor from 20 and
        # widened its own canary (verb_mask_selftest) but not this fixture, which
        # left the test red on main.
        p = self.body("here is a leak ghp_" + "abcdefgh" * 4 + "abcd\n")
        ok, rule, *_, decision = D.handle(
            self.preq("pr-comment", ["lumen/lumen-bin", "7", p],
                      kind="pr_comment"), self.peer)
        self.assertEqual((ok, rule, decision), (False, "body_mask_tripwire", "deny"))

    def test_pr_open_creates_when_no_existing(self):
        calls = []

        def fake_http(method, base, path, scheme, token, body=None):
            calls.append((method, path, body))
            if method == "GET":
                return 200, []
            return 201, {"number": 12, "html_url": "http://forge/pr/12"}

        D.http_json = fake_http
        title, bodyf = self.body("feat(x): y\n", "t.txt"), self.body()
        ok, rule, _, result, decision = D.handle(
            self.preq("pr-open",
                      ["lumen/lumen-bin", "feat/x", "main", title, bodyf]),
            self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual((result["number"], result["action"]), (12, "created"))
        self.assertEqual(calls[-1][0], "POST")
        self.assertEqual(calls[-1][2]["head"], "feat/x")

    def test_pr_open_updates_existing_matched_on_head_ref(self):
        # wq#557 port: Gitea ignores head=, so the match must be client-side
        # on head.ref — an open PR for a DIFFERENT branch must not be patched.
        def fake_http(method, base, path, scheme, token, body=None):
            if method == "GET":
                return 200, [{"number": 3, "head": {"ref": "other-branch"}},
                             {"number": 4, "head": {"ref": "feat/x"}}]
            self.assertIn("/pulls/4", path)
            return 200, {"number": 4, "html_url": "http://forge/pr/4"}

        D.http_json = fake_http
        title, bodyf = self.body("feat(x): y\n", "t.txt"), self.body()
        ok, rule, _, result, _ = D.handle(
            self.preq("pr-open",
                      ["lumen/lumen-bin", "feat/x", "main", title, bodyf]),
            self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual((result["number"], result["action"]), (4, "updated"))

    def test_pr_merge_gitea_uses_do_and_delete_branch(self):
        merged = {}

        def fake_http(method, base, path, scheme, token, body=None):
            if method == "GET" and path.endswith("/commits"):
                return 200, [{"commit": {"message": "feat: x\n\nRefs: wq#1\n"}}]
            if method == "GET":
                return 200, {"state": "open", "merged": False, "title": "feat: x",
                             "head": {"sha": "a" * 40, "ref": "feat/x"}}
            merged.update(method=method, path=path, body=body)
            return 200, {}

        D.http_json = fake_http
        ok, rule, _, result, _ = D.handle(
            self.preq("pr-merge", ["lumen/lumen-bin", "9", "squash"]), self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual(merged["method"], "POST")
        self.assertEqual(merged["body"]["do"], "squash")
        self.assertTrue(merged["body"]["delete_branch_after_merge"])
        self.assertEqual(merged["body"]["merge_message_field"], "Refs: wq#1")
        self.assertTrue(result["merged"])

    def test_result_leaves_stay_scalar(self):
        # assert_response_schema forbids nested leaves; every PR verb result
        # must pass it or respond() replaces the frame with an error.
        D.http_json = lambda *a, **k: (200, [])
        ok, rule, _, result, _ = D.handle(
            self.preq("pr-list", ["lumen/lumen-bin"], kind="pr_read"), self.peer)
        self.assertTrue(ok, rule)
        D.assert_response_schema({"ok": True, "verb": "pr-list", "ticket": "wq2270",
                                  "rule": "r", "reason": "", "result": result,
                                  "elapsed_s": 0.0})


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
        # wq#2300: split at the boundary the kv rule reads, as test_masking.py's
        # fixtures are (wq#2299/#2303) — whole, this line arms the tripwire on every
        # worker that READS this file, which is how wq#2278 filed wq#2289.
        self.assertIn("kv_secret", D.mask_hits('TOKEN="' + 'abcdefgh12345678"'))
        self.assertEqual(D.mask_hits('{"pong": true, "daemon_uid": 998}'), [])

    def test_mask_ignores_prefixes_inside_words(self):
        """wq#2292: a tripwire hit FAILS the verb, so `ghs_` inside the repo name
        `highs_solver` must not match — that false positive is an outage, and it
        already cost one pri-1 rotation ticket on the fworker copy of this set."""
        for text in ('{"reason": "repo highs_solver has no remote"}',
                     '{"reason": "clone ghp_notes.git"}',
                     '{"reason": "risk-assessment-framework"}',
                     '{"reason": "ghp_MASKED"}'):
            self.assertEqual(D.mask_hits(text), [], text)

    def test_mask_does_not_read_a_called_identifier_as_a_secret(self):
        # wq#2288: this shape cost a pri-1 rotation ticket in the harness copy of the
        # rule; here a false hit fails the verb and drops the caller's result.
        self.assertEqual(D.mask_hits("key = apply_key_allowlist(reg)"), [])
        self.assertEqual(D.mask_hits('TOKEN = os.environ.get("GH_TOKEN")'), [])
        self.assertIn("kv_secret", D.mask_hits("password: " + "hunter2-hunter2-hunter2"))


class T10Base(unittest.TestCase):
    """Shared fixture for the wq#2278 verbs: tempdir engine store, providers
    config override, no network (http_json / subprocess.run stubbed per test).
    Fixture values are concatenation-built so READING this file never matches
    a mask pattern (wq#2290/wq#2289: attempt 1's rotation-ticket cascade was
    exactly that)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.peer = (1234, 1000, 1000)
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(os.path.join(self.state, "secrets"))
        self.orig = {n: getattr(D, n) for n in
                     ("SECRETS_DIR", "PROVIDERS_PATH", "POLICY_PATH",
                      "SOCK_DIR", "BODY_ROOT", "http_json")}
        D.SECRETS_DIR = self.state
        D.SOCK_DIR = os.path.join(self.tmp.name, "run")
        D.BODY_ROOT = self.tmp.name
        D.PROVIDERS_PATH = os.path.join(self.tmp.name, "providers.json")
        with open(D.PROVIDERS_PATH, "w") as f:
            json.dump({
                "providers": {"testprov": {"auth": "bearer",
                                           "base_url": "https://api.example.com",
                                           "token_file": "testprov.token"}},
                "gateway": {"base_url": "http://localhost:4000",
                            "admin_key_file": "litellm-admin.token",
                            "max_budget_usd": 25.0, "max_ttl_s": 259200},
                "plug_creds_file": "tapo.env",
                "plugs": {"p9": "192.0.2.9"}}, f)
        self.orig_sub = D.subprocess.run
        os.environ["TOKEN_ENGINE_STATE_DIR"] = self.state
        D.L.MINTED.clear()

    def tearDown(self):
        for n, v in self.orig.items():
            setattr(D, n, v)
        D.subprocess.run = self.orig_sub
        del os.environ["TOKEN_ENGINE_STATE_DIR"]
        self.tmp.cleanup()

    def store(self, basename, content):
        p = os.path.join(self.state, "secrets", basename)
        with open(p, "w") as f:
            f.write(content + "\n")
        return p

    def vreq(self, verb, argv, kind, node, extra_grant=None):
        grant = {"ticket": "wq2270", "expires": "2099-01-01T00:00:00Z",
                 "grants": [dict({"kind": kind, "node": node},
                                 **(extra_grant or {}))]}
        raw = json.dumps(grant).encode()
        path = os.path.join(self.tmp.name, "grant.json")
        with open(path, "wb") as f:
            f.write(raw)
        r = req(path, hashlib.sha256(raw).hexdigest(), verb=verb)
        r["argv"] = argv
        return r


class ApiCallTest(T10Base):
    def setUp(self):
        super().setUp()
        self.store("testprov.token", "tp-store-only-value")

    def test_grant_scoped_per_provider(self):
        # kind AND node derive from argv[0]: an api:testprov grant is useless
        # against any other provider name.
        r = self.vreq("api-call", ["otherprov", "GET", "/v1/x"],
                      "api:testprov", "api:testprov")
        ok, rule, *_, decision = D.handle(r, self.peer)
        self.assertEqual((ok, rule, decision), (False, "grant_no_match", "deny"))

    def test_unknown_provider_denied(self):
        r = self.vreq("api-call", ["nosuch", "GET", "/v1/x"],
                      "api:nosuch", "api:nosuch")
        ok, rule, *_, decision = D.handle(r, self.peer)
        self.assertEqual((ok, rule, decision), (False, "provider_unknown", "deny"))

    def test_method_and_path_shapes(self):
        for argv, want in ((["testprov", "TRACE", "/v1/x"], "argv_malformed"),
                           (["testprov", "GET", "v1-no-slash"], "argv_malformed"),
                           (["testprov", "GET", "/v1/../admin"], "argv_malformed"),
                           (["testprov", "GET"], "argv_arity")):
            ok, rule, *_ , _d = D.handle(
                self.vreq("api-call", argv, "api:testprov", "api:testprov"), self.peer)
            self.assertEqual((ok, rule), (False, want), argv)

    def test_response_is_masked_and_carries_no_auth(self):
        seen = {}

        def fake_http(method, base, path, scheme, token, body=None):
            seen.update(method=method, base=base, path=path, token=token)
            return 200, {"echo": "ghp_" + "b" * 36, "note": "fine"}

        D.http_json = fake_http
        ok, rule, _, result, _ = D.handle(
            self.vreq("api-call", ["testprov", "GET", "/v1/x"],
                      "api:testprov", "api:testprov"), self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual(seen["token"], "tp-store-only-value")  # header built inside
        self.assertEqual(result["status"], 200)
        self.assertIn("MASKED", result["body"])              # rewrite, §2 api-call row
        dumped = json.dumps(result, sort_keys=True)
        self.assertNotIn(seen["token"], dumped)              # never the credential
        self.assertNotIn("Authorization", dumped)            # never the header
        self.assertEqual(D.mask_hits(dumped), [])            # tripwire would stay quiet
        D.assert_response_schema({"ok": True, "verb": "api-call", "ticket": "wq2270",
                                  "rule": "r", "reason": "", "result": result,
                                  "elapsed_s": 0.0})

    def test_body_file_must_be_json(self):
        p = os.path.join(self.tmp.name, "body.txt")
        with open(p, "w") as f:
            f.write("not json")
        ok, rule, *_ , _d = D.handle(
            self.vreq("api-call", ["testprov", "POST", "/v1/x", p],
                      "api:testprov", "api:testprov"), self.peer)
        self.assertEqual((ok, rule), (False, "body_not_json"))


class GatewayKeyTest(T10Base):
    KEYVAL = "sk-" + "minted" * 5  # sk-key shaped at runtime, split in source

    def setUp(self):
        super().setUp()
        self.store("litellm-admin.token", "adm-store-only-value")
        self.orig_revokers = dict(D.L.REVOKERS)

    def tearDown(self):
        D.L.REVOKERS.clear()
        D.L.REVOKERS.update(self.orig_revokers)
        super().tearDown()

    def mint(self, argv=("mint", "worker", "24h", "15.0")):
        D.http_json = lambda *a, **k: (
            200, {"key": self.KEYVAL, "expires": "2026-09-21T00:00:00Z"})
        return D.handle(self.vreq("gateway-key", list(argv),
                                  "gateway_admin", "gateway:litellm"), self.peer)

    def test_mint_returns_id_expiry_path_never_the_value(self):
        ok, rule, _, result, _ = self.mint()
        self.assertTrue(ok, rule)
        self.assertTrue(result["key_id"].startswith("gwk-"))
        self.assertEqual(result["expires"], "2026-09-21T00:00:00Z")
        dumped = json.dumps(result, sort_keys=True)
        self.assertNotIn(self.KEYVAL, dumped)
        self.assertEqual(D.mask_hits(dumped), [])
        # the value reaches the client ONLY via the 0640 handoff file
        with open(result["key_file"]) as f:
            self.assertEqual(f.read().strip(), self.KEYVAL)
        self.assertEqual(os.stat(result["key_file"]).st_mode & 0o777, 0o640)

    def test_caps_enforced(self):
        for argv, want in ((("mint", "w", "24h", "100"), "gateway_budget_cap"),
                           (("mint", "w", "31d", "5"), "gateway_ttl_cap"),
                           (("mint", "w", "forever", "5"), "argv_malformed")):
            ok, rule, *_ , _d = self.mint(argv)
            self.assertEqual((ok, rule), (False, want), argv)

    def test_ticket_end_revokes_the_minted_key(self):
        revoked = []
        D.L.REVOKERS["litellm-key"] = lambda rv: (revoked.append(rv) or True, "ok")
        ok, rule, _, result, _ = self.mint()
        self.assertTrue(ok, rule)
        ok2, rule2, _, r2, _ = D.handle(
            self.vreq("ticket-end", [], "token_engine", "daemon"), self.peer)
        self.assertTrue(ok2, rule2)
        self.assertEqual((r2["had_entries"], r2["revoked"]), (1, 1))
        self.assertEqual(revoked[0]["key"], self.KEYVAL)

    def test_revoke_verb_is_idempotent(self):
        D.L.REVOKERS["litellm-key"] = lambda rv: (True, "ok")
        ok, _, _, result, _ = self.mint()
        self.assertTrue(ok)
        kid = result["key_id"]
        ok, rule, _, r1, _ = D.handle(
            self.vreq("gateway-key", ["revoke", kid],
                      "gateway_admin", "gateway:litellm"), self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual((r1["found"], r1["revoked"]), (1, 1))
        _, _, _, r2, _ = D.handle(
            self.vreq("gateway-key", ["revoke", kid],
                      "gateway_admin", "gateway:litellm"), self.peer)
        self.assertEqual(r2["found"], 0)


class PlugTest(T10Base):
    def setUp(self):
        super().setUp()
        self.store("tapo.env", "TAPO_USER=u@example.com\nTAPO_PASS=plug-cred-value")

    def test_kasa_gets_creds_via_env_never_argv(self):
        calls = {}

        def fake_run(argv, **kw):
            calls["argv"], calls["env"] = argv, kw.get("env")
            return subprocess.CompletedProcess(argv, 0, "Device state: True\n", "")

        D.subprocess.run = fake_run
        ok, rule, _, result, _ = D.handle(
            self.vreq("plug", ["p9", "state"], "plug", "plug:p9"), self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual(result["state"], "on")
        self.assertIn("192.0.2.9", calls["argv"])
        self.assertNotIn("plug-cred-value", " ".join(calls["argv"]))  # /proc-safe
        self.assertEqual(calls["env"]["KASA_PASSWORD"], "plug-cred-value")
        self.assertEqual(D.mask_hits(json.dumps(result, sort_keys=True)), [])

    def test_unmapped_and_malformed_plugs_denied(self):
        ok, rule, *_ , _d = D.handle(
            self.vreq("plug", ["p8", "on"], "plug", "plug:p8"), self.peer)
        self.assertEqual((ok, rule), (False, "plug_unknown"))
        ok, rule, *_ , _d = D.handle(
            self.vreq("plug", ["lamp", "on"], "plug", "plug:lamp"), self.peer)
        self.assertEqual((ok, rule), (False, "plug_malformed"))

    def test_grant_scoped_per_plug(self):
        ok, rule, *_ , _d = D.handle(
            self.vreq("plug", ["p9", "on"], "plug", "plug:p1"), self.peer)
        self.assertEqual((ok, rule), (False, "grant_no_match"))


class SshRunTest(T10Base):
    def setUp(self):
        super().setUp()
        self.key = self.store("fleet-ssh.key", "dummy-key-material")
        D.POLICY_PATH = os.path.join(self.tmp.name, "policy.yaml")
        with open(D.POLICY_PATH, "w") as f:
            f.write("never:\n  nodes: []\nrun_readonly_cmds:\n  - uptime\n")

    def test_ssh_runs_with_the_engine_key(self):
        calls = {}

        def fake_run(argv, **kw):
            calls["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "up 3 days", "")

        D.subprocess.run = fake_run
        ok, rule, _, result, _ = D.handle(
            self.vreq("ssh-run", ["bes", "uptime"], "ssh", "bes",
                      {"verbs": ["run"]}), self.peer)
        self.assertTrue(ok, rule)
        self.assertEqual(result["exit_code"], 0)
        i = calls["argv"].index("-i")
        self.assertEqual(calls["argv"][i + 1], self.key)  # engine store, not ~/.ssh

    def test_run_verb_required_in_grant(self):
        ok, rule, *_ , _d = D.handle(
            self.vreq("ssh-run", ["bes", "uptime"], "ssh", "bes",
                      {"verbs": ["copy"]}), self.peer)
        self.assertEqual((ok, rule), (False, "grant:no_ssh_run"))

    def test_policy_readonly_allowlist_still_binds(self):
        # same check_run fleet-exec applies: moving the key must not widen
        # what a worker can execute.
        ok, rule, *_ , _d = D.handle(
            self.vreq("ssh-run", ["bes", "reboot"], "ssh", "bes",
                      {"verbs": ["run"]}), self.peer)
        self.assertEqual(ok, False)
        self.assertTrue(rule.startswith("policy:"), rule)

    def test_grant_scoped_per_node(self):
        ok, rule, *_ , _d = D.handle(
            self.vreq("ssh-run", ["maat", "uptime"], "ssh", "bes",
                      {"verbs": ["run"]}), self.peer)
        self.assertEqual((ok, rule), (False, "grant_no_match"))


class MaskRewriteTest(unittest.TestCase):
    def test_rewrite_output_never_retrips_the_tripwire(self):
        dirty = ("cf token cfut_" + "abcdef123456" + " and pat ghp_" + "c" * 36 +
                 " in one body")
        clean = D.mask_rewrite(dirty)
        self.assertIn("MASKED", clean)
        self.assertEqual(D.mask_hits(clean), [])

    def test_rewrite_leaves_clean_text_alone(self):
        self.assertEqual(D.mask_rewrite("plain provider output"),
                         "plain provider output")


if __name__ == "__main__":
    unittest.main(verbosity=2)
