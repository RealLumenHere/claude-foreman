#!/usr/bin/env python3
"""Unit tests for claude_shim.py's pure functions — no live CLI, no network.

Run: python3 -m pytest claude-shim/tests/test_claude_shim.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_shim as shim  # noqa: E402


class TestFormatPrompt(unittest.TestCase):
    def test_system_message_extracted(self):
        sysp, prompt = shim._format_prompt([
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "hi"},
        ])
        self.assertEqual(sysp, "Be terse.")
        self.assertEqual(prompt, "User: hi")

    def test_no_system_message_uses_default(self):
        sysp, prompt = shim._format_prompt([{"role": "user", "content": "hi"}])
        self.assertEqual(sysp, "You are Claude.")

    def test_multi_turn_transcript(self):
        _, prompt = shim._format_prompt([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "bye"},
        ])
        self.assertEqual(prompt, "User: hi\n\nAssistant: hello\n\nUser: bye")

    def test_only_first_system_message_kept(self):
        sysp, _ = shim._format_prompt([
            {"role": "system", "content": "first"},
            {"role": "system", "content": "second"},
            {"role": "user", "content": "hi"},
        ])
        self.assertEqual(sysp, "first")


class TestValidateRequest(unittest.TestCase):
    def test_unknown_model_rejected(self):
        with self.assertRaises(shim.ShimError) as cm:
            shim._validate_request({"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(cm.exception.status, 400)

    def test_stream_rejected(self):
        with self.assertRaises(shim.ShimError) as cm:
            shim._validate_request({"model": "claude-max-sonnet", "stream": True,
                                     "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(cm.exception.status, 400)

    def test_tools_rejected(self):
        with self.assertRaises(shim.ShimError):
            shim._validate_request({"model": "claude-max-sonnet", "tools": [{"type": "function"}],
                                     "messages": [{"role": "user", "content": "hi"}]})

    def test_valid_request_passes(self):
        model, alias, messages = shim._validate_request(
            {"model": "claude-max-opus", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(model, "claude-max-opus")
        self.assertEqual(alias, "opus")

    def test_ignored_params_do_not_raise(self):
        model, alias, _ = shim._validate_request({
            "model": "claude-max-sonnet", "max_tokens": 100, "temperature": 0.7,
            "messages": [{"role": "user", "content": "hi"}],
        })
        self.assertEqual(alias, "sonnet")


class TestClassifyResult(unittest.TestCase):
    def test_success(self):
        stdout = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "hi there"})
        status, err_type, text, _ = shim._classify_cli_result(0, stdout, "")
        self.assertEqual(status, 200)
        self.assertEqual(text, "hi there")

    def test_empty_stdout_is_crash(self):
        status, err_type, text, _ = shim._classify_cli_result(1, "", "boom")
        self.assertEqual(status, 502)
        self.assertEqual(err_type, "cli_crash")

    def test_unparseable_stdout_is_crash(self):
        status, err_type, _, _ = shim._classify_cli_result(0, "not json", "")
        self.assertEqual(status, 502)
        self.assertEqual(err_type, "cli_crash")

    def test_max_turns_is_500(self):
        stdout = json.dumps({"is_error": True, "subtype": "error_max_turns", "result": "x"})
        status, err_type, _, _ = shim._classify_cli_result(1, stdout, "")
        self.assertEqual(status, 500)
        self.assertEqual(err_type, "cli_max_turns")

    def test_generic_error_is_502(self):
        stdout = json.dumps({"is_error": True, "subtype": "error_during_execution", "result": "weird failure"})
        status, err_type, _, _ = shim._classify_cli_result(1, stdout, "")
        self.assertEqual(status, 502)
        self.assertEqual(err_type, "cli_error")


class TestAllowlist(unittest.TestCase):
    def test_loopback_allowed(self):
        from ipaddress import ip_address
        self.assertTrue(any(ip_address("127.0.0.1") in net for net in shim.ALLOWED_NETS))

    def test_docker_bridge_allowed(self):
        from ipaddress import ip_address
        self.assertTrue(any(ip_address("172.18.0.2") in net for net in shim.ALLOWED_NETS))

    def test_lan_rejected(self):
        from ipaddress import ip_address
        self.assertFalse(any(ip_address("192.168.8.50") in net for net in shim.ALLOWED_NETS))


if __name__ == "__main__":
    unittest.main()
