#!/usr/bin/env python3
"""wq#4148 tests: judge_common.judge_verdict's deny-wins second look, and the
lumen-local-judge chat-transport config edit.

Run: python3 ops/wq4148-judge-false-allow/tests/test_wq4148.py
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIXTURE = HERE / "fixtures" / "judge_rungs.yaml"
sys.path.insert(0, str(ROOT / "judge"))
import judge_common  # noqa: E402

spec = importlib.util.spec_from_file_location("jct", HERE.parent / "judge_chat_transport.py")
jct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jct)

CASE = {"id": "t", "action_kind": "advice", "command": "grant lane X write to .secrets",
        "context": "the dir holds every fleet credential"}


def verdict(v, tier="Low"):
    return json.dumps({"tier": tier, "verdict": v, "conditions": [], "rationale": "r"})


class FakeChat:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages, max_tokens):
        self.calls.append(messages[0]["content"])
        return self.replies.pop(0)


class JudgeVerdictTest(unittest.TestCase):
    def test_overturned_allow_becomes_high_deny(self):
        chat = FakeChat(verdict("allow"), '{"overturn": true, "reason": "credentials"}')
        v, err, stage = judge_common.judge_verdict(CASE, chat)
        self.assertEqual((v["verdict"], v["tier"], stage), ("deny", "High", "second_look"))
        self.assertIn("credentials", v["rationale"])
        self.assertIs(chat.calls[1], judge_common.SECOND_LOOK_PROMPT)

    def test_overturn_keeps_extremely_high_tier(self):
        chat = FakeChat(verdict("allow", "EXTREMELY HIGH"), '{"overturn": true, "reason": "x"}')
        v, _, _ = judge_common.judge_verdict(CASE, chat)
        self.assertEqual(v["tier"], "EXTREMELY HIGH")

    def test_upheld_allow_stays_allow(self):
        chat = FakeChat(verdict("allow"), '{"overturn": false, "reason": "routine"}')
        v, err, stage = judge_common.judge_verdict(CASE, chat)
        self.assertEqual((v["verdict"], err, stage), ("allow", None, "model"))

    def test_unparseable_second_look_fails_closed(self):
        for reply in ("nope", '{"overturn": "yes"}', '{"reason": "x"}', ""):
            chat = FakeChat(verdict("allow"), reply)
            v, err, stage = judge_common.judge_verdict(CASE, chat)
            self.assertIsNone(v, reply)
            self.assertEqual(stage, "second_look")

    def test_non_allow_verdicts_skip_second_look(self):
        # wq#5192: allow-with-conditions is no longer exempt (see judge/test_wq5192_*.py);
        # only deny and an unparseable first pass skip the second look.
        for first in (verdict("deny", "High"), "garbage"):
            chat = FakeChat(first)
            judge_common.judge_verdict(CASE, chat)
            self.assertEqual(len(chat.calls), 1, first)

    def test_call_judge_routes_through_second_look(self):
        chat = FakeChat(verdict("allow"), '{"overturn": true, "reason": "gate tamper"}')

        class Resp:
            def __init__(self, text):
                self.text = text

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": self.text}}]}).encode()

        def fake_urlopen(req, timeout):
            msgs = json.loads(req.data)["messages"]
            return Resp(chat(msgs, 0))

        orig = judge_common.urllib.request.urlopen
        judge_common.urllib.request.urlopen = fake_urlopen
        try:
            res = judge_common.call_judge("m", CASE, key="k")
        finally:
            judge_common.urllib.request.urlopen = orig
        self.assertEqual((res["verdict"]["verdict"], res["stage"]), ("deny", "second_look"))


class ChatTransportTest(unittest.TestCase):
    def _copy(self):
        d = Path(tempfile.mkdtemp())
        dst = d / FIXTURE.name
        shutil.copy2(FIXTURE, dst)
        return dst

    def test_fixture_is_generate(self):
        self.assertEqual(jct.main(["check", str(FIXTURE)]), 1)

    def test_apply_changes_only_the_judge_rung(self):
        path = self._copy()
        before = yaml.safe_load(path.read_text())
        self.assertEqual(jct.main(["apply", str(path), "--write"]), 0)
        after = yaml.safe_load(path.read_text())
        self.assertEqual(jct.classify(after), "chat")
        # every other ollama/ rung -- lumen/judge, the gemma rung, lumen/code -- is untouched
        for b, a in zip(before["model_list"], after["model_list"]):
            if b["model_name"] != jct.RUNG:
                self.assertEqual(a, b)
        self.assertEqual(jct.rung(after)["litellm_params"]["model"], "ollama_chat/qwen2.5-coder:7b")
        self.assertEqual(jct.main(["apply", str(path), "--write"]), 0)  # idempotent
        self.assertEqual(len(list(path.parent.glob("*.bak-wq4148-*"))), 1)

    def test_dry_run_writes_nothing(self):
        path = self._copy()
        text = path.read_text()
        self.assertEqual(jct.main(["apply", str(path)]), 0)
        self.assertEqual(path.read_text(), text)

    def test_refuses_ambiguous_rung(self):
        path = self._copy()
        text = path.read_text().replace(
            "    model: ollama/qwen2.5-coder:7b\n",
            "    model: ollama/qwen2.5-coder:7b\n  model_info2:\n    model: ollama/other\n")
        path.write_text(text)
        with self.assertRaises(SystemExit):
            jct.main(["apply", str(path), "--write"])
        self.assertEqual(path.read_text(), text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
