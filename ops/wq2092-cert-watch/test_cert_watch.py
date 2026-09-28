"""wq#2092 — cert_watch tests. Offline: a loopback TLS server with a throwaway
openssl cert stands in for a fleet endpoint; ntfy/aq are stubbed."""
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import cert_watch as cw  # noqa: E402

DAY = 86400
NOW = 1_800_000_000.0

CF_CONFIG = """\
tunnel: abc
ingress:
  - hostname: home.lumennode.uk
    service: http://localhost:8080
  - hostname: "*.lumennode.uk"
    service: http://localhost:1
  - hostname: vault.lumennode.uk # trailing comment
    service: http://localhost:8000
  - hostname: home.lumennode.uk
    service: http://localhost:8080
  - service: http_status:404
"""


class ClassifyTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(cw.classify(30), "ok")
        self.assertEqual(cw.classify(21.5), "ok")
        self.assertEqual(cw.classify(21), "21d")
        self.assertEqual(cw.classify(7.1), "21d")
        self.assertEqual(cw.classify(7), "7d")
        self.assertEqual(cw.classify(1.5), "7d")
        self.assertEqual(cw.classify(1), "1d")
        self.assertEqual(cw.classify(0.2), "1d")
        self.assertEqual(cw.classify(0), "expired")
        self.assertEqual(cw.classify(-3), "expired")


class ParseTests(unittest.TestCase):
    def test_hostnames_skip_wildcard_dupes_and_catchall(self):
        self.assertEqual(cw.parse_hostnames(CF_CONFIG),
                         ["home.lumennode.uk", "vault.lumennode.uk"])

    def test_endpoint(self):
        self.assertEqual(cw.parse_endpoint("a.example"), ("a.example", 443))
        self.assertEqual(cw.parse_endpoint("a.example:8443"), ("a.example", 8443))

    def test_extra_targets_missing_file_is_empty(self):
        self.assertEqual(cw.load_extra_targets("/nonexistent/targets.json"), [])


class CheckHostTests(unittest.TestCase):
    """Tier-transition logic with fetch_not_after stubbed."""

    def check(self, days_left, prev, aq_id=7):
        with mock.patch.object(cw, "fetch_not_after",
                               return_value=(NOW + days_left * DAY, None)), \
             mock.patch.object(cw, "ntfy") as ntfy, \
             mock.patch.object(cw, "aq_ingest", return_value=aq_id) as ingest, \
             mock.patch.object(cw, "aq_resolve") as resolve:
            entry = cw.check_host("h.example", 443, prev, NOW, dry_run=False)
        return entry, ntfy, ingest, resolve

    def test_first_sight_healthy_is_quiet(self):
        entry, ntfy, ingest, _ = self.check(60, {})
        self.assertEqual(entry["tier"], "ok")
        ntfy.assert_not_called()
        ingest.assert_not_called()

    def test_crossing_21d_alerts_default_priority(self):
        entry, ntfy, ingest, _ = self.check(20, {"tier": "ok"})
        self.assertEqual(entry["tier"], "21d")
        self.assertEqual(entry["aq_id"], 7)
        self.assertEqual(ntfy.call_args.kwargs["priority"], "default")
        self.assertEqual(ingest.call_args.args[0], "h.example:443")

    def test_standing_tier_does_not_realert(self):
        entry, ntfy, ingest, _ = self.check(20, {"tier": "21d", "aq_id": 7})
        ntfy.assert_not_called()
        ingest.assert_not_called()
        self.assertEqual(entry["aq_id"], 7)

    def test_escalation_7d_then_1d_then_expired(self):
        for days, prev, tier, prio in [(6, "21d", "7d", "high"),
                                       (0.5, "7d", "1d", "urgent"),
                                       (-1, "1d", "expired", "urgent")]:
            entry, ntfy, _, _ = self.check(days, {"tier": prev})
            self.assertEqual(entry["tier"], tier)
            self.assertEqual(ntfy.call_args.kwargs["priority"], prio)

    def test_expired_message_says_expired(self):
        _, ntfy, _, _ = self.check(-2, {"tier": "ok"})
        self.assertIn("EXPIRED", ntfy.call_args.args[0])

    def test_first_sight_already_in_7d_alerts_once(self):
        entry, ntfy, _, _ = self.check(5, {})
        self.assertEqual(entry["tier"], "7d")
        ntfy.assert_called_once()

    def test_renewal_notifies_and_resolves_aq(self):
        entry, ntfy, _, resolve = self.check(80, {"tier": "7d", "aq_id": 7})
        self.assertEqual(entry["tier"], "ok")
        self.assertIsNone(entry["aq_id"])
        self.assertIn("renewed", ntfy.call_args.args[0])
        resolve.assert_called_once_with(7, "cert renewed")

    def unreachable(self, prev):
        with mock.patch.object(cw, "fetch_not_after", return_value=(None, "boom")), \
             mock.patch.object(cw, "ntfy") as ntfy, \
             mock.patch.object(cw, "aq_ingest") as ingest, \
             mock.patch.object(cw, "aq_resolve") as resolve:
            entry = cw.check_host("h.example", 443, prev, NOW, dry_run=False)
        return entry, ntfy, ingest, resolve

    def test_unreachable_recorded_without_cert_alert(self):
        entry, ntfy, ingest, resolve = self.unreachable({})
        self.assertEqual(entry["tier"], "unreachable")  # never seen good
        self.assertEqual(entry["error"], "boom")
        ntfy.assert_not_called()
        ingest.assert_not_called()
        resolve.assert_not_called()

    def test_unreachable_carries_prev_tier_and_aq_id_forward(self):
        """wq#2983: a failed fetch must not erase what the last good run knew."""
        prev = {"tier": "7d", "not_after": NOW + 5 * DAY, "days_left": 5.0, "aq_id": 7}
        entry, _, _, _ = self.unreachable(prev)
        self.assertEqual(entry["tier"], "7d")
        self.assertEqual(entry["aq_id"], 7)
        self.assertEqual(entry["not_after"], prev["not_after"])
        self.assertEqual(entry["days_left"], 5.0)
        self.assertEqual(entry["error"], "boom")
        self.assertEqual(entry["error_count"], 1)

    def test_consecutive_unreachable_runs_count_up(self):
        entry, _, _, _ = self.unreachable({"tier": "7d", "error_count": 2, "error": "boom"})
        self.assertEqual(entry["error_count"], 3)

    def test_wq2983_a_unreachable_then_same_tier_does_not_repage(self):
        """A: 7d alert -> failed fetch -> same 7d cert must stay silent."""
        entry, ntfy, ingest, _ = self.check(5, {})           # first sight in 7d
        ntfy.assert_called_once()
        ingest.assert_called_once()
        entry, _, _, _ = self.unreachable(entry)             # transient failure
        entry, ntfy, ingest, _ = self.check(5, entry)        # same cert, reachable
        self.assertEqual(entry["tier"], "7d")
        self.assertEqual(entry["aq_id"], 7)
        ntfy.assert_not_called()
        ingest.assert_not_called()

    def test_wq2983_b_unreachable_then_renewal_resolves_aq(self):
        """B: 7d alert -> failed fetch -> renewed cert must notify and resolve."""
        entry, _, _, _ = self.check(5, {})
        entry, _, _, _ = self.unreachable(entry)
        entry, ntfy, _, resolve = self.check(90, entry)
        self.assertEqual(entry["tier"], "ok")
        self.assertIsNone(entry["aq_id"])
        self.assertIn("renewed", ntfy.call_args.args[0])
        resolve.assert_called_once_with(7, "cert renewed")

    def test_dry_run_skips_aq(self):
        with mock.patch.object(cw, "fetch_not_after", return_value=(NOW + 3 * DAY, None)), \
             mock.patch.object(cw, "ntfy"), \
             mock.patch.object(cw, "aq_ingest") as ingest:
            cw.check_host("h.example", 443, {}, NOW, dry_run=True)
        ingest.assert_not_called()


def make_cert(tmp, days):
    """Self-signed cert valid from now for `days` days."""
    key, crt = os.path.join(tmp, "k.pem"), os.path.join(tmp, "c.pem")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
         "-out", crt, "-subj", "/CN=localhost", "-days", str(days)],
        check=True, capture_output=True)
    return key, crt


class LiveTlsTests(unittest.TestCase):
    """Real handshake against a loopback TLS server (verification off)."""

    def serve(self, key, crt):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def loop():
            try:
                conn, _ = srv.accept()
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except OSError:
                pass
            finally:
                srv.close()

        threading.Thread(target=loop, daemon=True).start()
        return srv.getsockname()[1]

    def test_reads_not_after_from_self_signed_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, crt = make_cert(tmp, 10)
            port = self.serve(key, crt)
            not_after, err = cw.fetch_not_after("127.0.0.1", port)
        self.assertIsNone(err)
        self.assertAlmostEqual((not_after - time.time()) / DAY, 10, delta=0.1)

    def test_closed_port_is_error_not_crash(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        not_after, err = cw.fetch_not_after("127.0.0.1", port, timeout=2)
        self.assertIsNone(not_after)
        self.assertIn("Error", err)


class RunTests(unittest.TestCase):
    def test_run_persists_state_and_alerts_once_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg, state = os.path.join(tmp, "cf.yml"), os.path.join(tmp, "s.json")
            with open(cfg, "w") as f:
                f.write("ingress:\n  - hostname: a.example\n    service: x\n")
            kw = dict(cf_config=cfg, targets_path=os.path.join(tmp, "none.json"),
                      extra=["b.example:8443"], state_path=state, now=NOW)
            with mock.patch.object(cw, "read_cloudflared_hosts",
                                   return_value=(["a.example"], None)), \
                 mock.patch.object(cw, "fetch_not_after",
                                   side_effect=lambda h, p, **_: (NOW + 5 * DAY, None)), \
                 mock.patch.object(cw, "ntfy") as ntfy, \
                 mock.patch.object(cw, "aq_ingest", return_value=1):
                self.assertEqual(cw.run(**kw), 0)
                self.assertEqual(ntfy.call_count, 2)  # a.example:443 + b.example:8443
                self.assertEqual(cw.run(**kw), 0)
                self.assertEqual(ntfy.call_count, 2)  # standing 7d tier: no repeat
            with open(state) as f:
                self.assertEqual(sorted(json.load(f)["hosts"]),
                                 ["a.example:443", "b.example:8443"])

    def test_all_unreachable_sends_one_aggregate_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            kw = dict(cf_config="x", targets_path=os.path.join(tmp, "n.json"),
                      state_path=os.path.join(tmp, "s.json"), now=NOW)
            with mock.patch.object(cw, "read_cloudflared_hosts",
                                   return_value=(["a.example", "b.example"], None)), \
                 mock.patch.object(cw, "fetch_not_after", return_value=(None, "down")), \
                 mock.patch.object(cw, "ntfy") as ntfy:
                cw.run(**kw)
                ntfy.assert_called_once()
                self.assertIn("2 endpoint(s) unreachable", ntfy.call_args.args[0])
                cw.run(**kw)  # still down: the transition already fired
                self.assertEqual(ntfy.call_count, 1)

    def test_no_targets_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(cw, "read_cloudflared_hosts",
                                   return_value=([], "cloudflared config unreadable")), \
                 mock.patch.object(cw, "ntfy"):
                rc = cw.run(cf_config="x", targets_path=os.path.join(tmp, "n.json"),
                            state_path=os.path.join(tmp, "s.json"), now=NOW)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
