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


if __name__ == "__main__":
    unittest.main()
