#!/usr/bin/env python3
"""wq#4111: pure-logic tests for probe.py's classifier, mocking the network boundary
so this runs without a live OpenHands/LiteLLM pair (CI, or a worker with no services
up yet)."""
import unittest
from unittest.mock import patch

import probe


class ClassifyApiRoutingTest(unittest.TestCase):
    def test_json_response_is_ok(self):
        with patch.object(probe, "_get", return_value=(200, {"content-type": "application/json"}, b"[]")):
            self.assertEqual(probe.classify_api_routing(), "ok")

    def test_spa_shell_is_flagged(self):
        html = b"<!DOCTYPE html><html><head><title>OpenHands</title></head></html>"
        with patch.object(probe, "_get", return_value=(200, {"content-type": "text/html; charset=utf-8"}, html)):
            self.assertEqual(probe.classify_api_routing(), "spa_fallback")

    def test_unreachable_is_down(self):
        with patch.object(probe, "_get", return_value=(None, {}, b"Connection refused")):
            self.assertEqual(probe.classify_api_routing(), "down")

    def test_unexpected_shape_is_unknown(self):
        with patch.object(probe, "_get", return_value=(500, {"content-type": "text/plain"}, b"boom")):
            self.assertEqual(probe.classify_api_routing(), "unknown")


class CheckOpenhandsAliveTest(unittest.TestCase):
    """wq#4149 item 7: lumen_state must probe /alive, not /api/health (which
    is SPA HTML with a misleading 200 — wq#4007 §1)."""

    def test_status_ok_body_is_alive(self):
        with patch.object(probe, "_get", return_value=(200, {}, b'{"status": "ok"}')):
            self.assertTrue(probe.check_openhands_alive())

    def test_non_200_is_not_alive(self):
        with patch.object(probe, "_get", return_value=(503, {}, b'{"status": "ok"}')):
            self.assertFalse(probe.check_openhands_alive())

    def test_spa_html_on_this_path_is_not_alive(self):
        # the /api/health failure mode this probe exists to avoid: 200 with a
        # body that isn't the liveness JSON shape at all.
        html = b"<!DOCTYPE html><html></html>"
        with patch.object(probe, "_get", return_value=(200, {}, html)):
            self.assertFalse(probe.check_openhands_alive())

    def test_unreachable_is_not_alive(self):
        with patch.object(probe, "_get", return_value=(None, {}, b"Connection refused")):
            self.assertFalse(probe.check_openhands_alive())


if __name__ == "__main__":
    unittest.main()
