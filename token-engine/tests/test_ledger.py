#!/usr/bin/env python3
"""Unit tests for token_ledger (wq#2277, T9): mint recording, ticket-end
revocation, idempotency, failed-entry retention, and the daemon-start reaper.
All against the test-file provider — no network, any uid.

    python3 token-engine/tests/test_ledger.py
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import token_ledger as L


def mint(ticket, tmp, token_id="tok1", provider="test-file"):
    live = os.path.join(tmp, "test-provider", f"{token_id}.live")
    os.makedirs(os.path.dirname(live), exist_ok=True)
    with open(live, "w") as f:
        f.write("live\n")
    L.record_mint(ticket, provider, token_id, "w1",
                  {"live_path": live}, "2026-09-20T00:00:00Z")
    return live


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TOKEN_ENGINE_STATE_DIR"] = self.tmp.name
        L.MINTED.clear()

    def tearDown(self):
        del os.environ["TOKEN_ENGINE_STATE_DIR"]
        self.tmp.cleanup()

    def test_revoke_ticket_kills_token_and_drops_map(self):
        live = mint("2277", self.tmp.name)
        self.assertEqual(L.MINTED["2277"], ["tok1"])
        r = L.revoke_ticket("2277")
        self.assertEqual(r, {"had_entries": 1, "revoked": 1, "failed": 0})
        self.assertFalse(os.path.exists(live))           # provider rejects it
        self.assertNotIn("2277", L.MINTED)               # in-memory map dropped
        self.assertFalse(os.path.exists(                 # ledger file gone
            os.path.join(self.tmp.name, "minted", "2277.json")))

    def test_revoke_is_idempotent(self):
        live = mint("2277", self.tmp.name)
        os.unlink(live)  # provider already considers it dead
        r1 = L.revoke_ticket("2277")
        self.assertEqual((r1["revoked"], r1["failed"]), (1, 0))
        r2 = L.revoke_ticket("2277")  # second call: nothing ledgered, still ok
        self.assertEqual(r2, {"had_entries": 0, "revoked": 0, "failed": 0})

    def test_failed_revocation_stays_ledgered(self):
        mint("2277", self.tmp.name)
        # live_path outside the state dir → revoker refuses → entry kept
        L.record_mint("2277", "test-file", "tok-evil", "w1",
                      {"live_path": "/etc/passwd"}, "2026-09-20T00:00:00Z")
        r = L.revoke_ticket("2277")
        self.assertEqual((r["had_entries"], r["revoked"], r["failed"]), (2, 1, 1))
        with open(os.path.join(self.tmp.name, "minted", "2277.json")) as f:
            kept = json.load(f)["entries"]
        self.assertEqual([e["token_id"] for e in kept], ["tok-evil"])
        self.assertEqual(L.MINTED["2277"], ["tok-evil"])

    def test_unknown_provider_kept_never_dropped(self):
        L.record_mint("2277", "test-file", "t1", "w1",
                      {"live_path": os.path.join(self.tmp.name, "x")},
                      "2026-09-20T00:00:00Z")
        path = os.path.join(self.tmp.name, "minted", "2277.json")
        with open(path) as f:
            doc = json.load(f)
        doc["entries"][0]["provider"] = "martian"
        with open(path, "w") as f:
            json.dump(doc, f)
        r = L.revoke_ticket("2277")
        self.assertEqual((r["revoked"], r["failed"]), (0, 1))
        self.assertTrue(os.path.exists(path))

    def test_reaper_sweeps_every_ticket(self):
        a = mint("2277", self.tmp.name, token_id="ta")
        b = mint("wq2278", self.tmp.name, token_id="tb")
        L.MINTED.clear()  # simulate daemon restart: memory gone, disk not
        totals = L.reap_all()
        self.assertEqual(totals, {"tickets": 2, "revoked": 2, "failed": 0})
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(b))
        self.assertEqual(L.reap_all(), {"tickets": 0, "revoked": 0, "failed": 0})

    def test_record_mint_rejects_bad_shapes(self):
        with self.assertRaises(ValueError):
            L.record_mint("../etc", "test-file", "t", "w", {}, "now")
        with self.assertRaises(ValueError):
            L.record_mint("2277", "no-such-provider", "t", "w", {}, "now")

    def test_github_app_idempotent_statuses(self):
        # 204/401/404 all count as revoked; 500 and network errors do not.
        for status, want in ((204, True), (401, True), (404, True), (500, False)):
            self.assertEqual(_gh_with_status(status)[0], want, status)

    def test_litellm_key_idempotent_statuses_and_handoff_unlink(self):
        # wq#2278: 200 = deleted, 400/404 = gateway already forgot the key;
        # 500/network keep the entry ledgered. The handoff file dies on every
        # attempt, success or not.
        secrets = os.path.join(self.tmp.name, "secrets")
        os.makedirs(secrets, exist_ok=True)
        with open(os.path.join(secrets, "litellm-admin.token"), "w") as f:
            f.write("adm-value\n")
        for status, want in ((200, True), (400, True), (404, True), (500, False)):
            handoff = os.path.join(self.tmp.name, "gwk-test.key")
            with open(handoff, "w") as f:
                f.write("value\n")
            ok, reason = _litellm_with_status(status, handoff)
            self.assertEqual(ok, want, status)
            self.assertFalse(os.path.exists(handoff), status)

    def test_litellm_key_admin_unreadable_fails_closed(self):
        ok, reason = L._revoke_litellm_key({"key": "x", "base_url": "http://gw"})
        self.assertFalse(ok)
        self.assertIn("admin key", reason)

    def test_revoke_one_scopes_to_the_named_token(self):
        mint("2278", self.tmp.name, token_id="keep")
        mint("2278", self.tmp.name, token_id="kill")
        r = L.revoke_one("2278", "kill")
        self.assertEqual(r, {"found": 1, "revoked": 1, "failed": 0})
        self.assertEqual(L.MINTED["2278"], ["keep"])
        self.assertEqual(L.revoke_one("2278", "kill"),
                         {"found": 0, "revoked": 0, "failed": 0})


def _gh_with_status(status):
    import unittest.mock as mock
    import urllib.error
    if status == 204:
        resp = mock.MagicMock()
        resp.status = 204
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        with mock.patch("urllib.request.urlopen", return_value=resp):
            return L._revoke_github_app({"token": "x"})
    err = urllib.error.HTTPError("u", status, "m", {}, None)
    with mock.patch("urllib.request.urlopen", side_effect=err):
        return L._revoke_github_app({"token": "x"})


def _litellm_with_status(status, handoff):
    import unittest.mock as mock
    import urllib.error
    revoke = {"key": "value", "base_url": "http://gw",
              "admin_key_file": "litellm-admin.token", "key_file": handoff}
    if status == 200:
        resp = mock.MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: resp
        resp.__exit__ = lambda s, *a: False
        with mock.patch("urllib.request.urlopen", return_value=resp):
            return L._revoke_litellm_key(revoke)
    err = urllib.error.HTTPError("u", status, "m", {}, None)
    with mock.patch("urllib.request.urlopen", side_effect=err):
        return L._revoke_litellm_key(revoke)


if __name__ == "__main__":
    unittest.main(verbosity=2)
