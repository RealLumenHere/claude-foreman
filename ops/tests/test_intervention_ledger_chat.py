#!/usr/bin/env python3
"""wq#1014 — chat-origin mining tests for the intervention ledger.
Runs standalone: python3 ops/tests/test_intervention_ledger_chat.py
No live DB or live transcripts are touched; everything is fixtures in tmp."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir))
import intervention_ledger as il


def _line(text, ts="2026-08-28T10:00:00.000Z", typ="user", sidechain=False,
          content=None, tool_result=False):
    o = {"type": typ, "timestamp": ts,
         "message": {"role": "user" if typ == "user" else "assistant",
                     "content": content if content is not None else text}}
    if sidechain:
        o["isSidechain"] = True
    if tool_result:
        o["toolUseResult"] = {"stdout": text}
        o["message"]["content"] = [{"type": "tool_result", "content": text}]
    return json.dumps(o)


def _write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


class ClassifyChat(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(il.classify_chat("LPP is down again?"), "detect")
        self.assertEqual(il.classify_chat("why is grafana failing"), "detect")
        self.assertEqual(il.classify_chat("gatus stopped working"), "detect")
        self.assertEqual(il.classify_chat("it's still broken"), "repeat")
        self.assertEqual(
            il.classify_chat("I already told you about the plug before"),
            "repeat")
        self.assertEqual(il.classify_chat("how many times do I have to say"),
                         "repeat")
        self.assertEqual(il.classify_chat("please fix the dispatcher"), "fix")
        self.assertEqual(il.classify_chat("revert that change"), "fix")
        self.assertIsNone(il.classify_chat("morning, how's the fleet"))
        self.assertIsNone(il.classify_chat("add a new widget to glance"))

    def test_precedence_detect_over_fix(self):
        # b > a: a message that both reports a fault and orders the fix lands
        # under the more damning kind.
        self.assertEqual(il.classify_chat("the bridge is dead, fix it now"),
                         "detect")


class ChatMining(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "projects")
        self._old_db = il.LEDGER_DB
        il.LEDGER_DB = os.path.join(self.tmp.name, "ledger.db")

    def tearDown(self):
        il.LEDGER_DB = self._old_db
        self.tmp.cleanup()

    def _interactive(self):
        _write(os.path.join(self.root, "-home-lumen-foreman",
                            "abc12345-0000.jsonl"), [
            _line("morning, how's the fleet"),
            _line("LPP is down again?"),
            _line("gatus looks dead too"),          # same kind+day → collapsed
            _line("please fix the dispatcher"),
            _line("ignore this", tool_result=True),
            _line("<command-name>/status</command-name>"),
            _line("<system-reminder>noise</system-reminder>"),
            _line("subagent turn: X is broken", sidechain=True),
            _line("assistant text", typ="assistant"),
            _line("revert that", ts="2026-08-29T09:00:00.000Z"),
        ])

    def test_events_and_burst_collapse(self):
        self._interactive()
        got = sorted(il.chat_events("2026-08-01", self.root))
        self.assertEqual([(g[0], g[1]) for g in got],
                         [("2026-08-28", "detect"), ("2026-08-28", "fix"),
                          ("2026-08-29", "fix")])
        self.assertIn("LPP is down", got[0][2])  # first message wins the desc
        self.assertTrue(all(g[3].startswith("chat:-home-lumen-foreman:abc12345")
                            for g in got))

    def test_headless_session_yields_nothing(self):
        # A worker transcript full of fault-words must not count as Frank.
        _write(os.path.join(self.root, "-home-lumen-foreman",
                            "def67890-0000.jsonl"), [
            _line("You are a Lumen fleet BUILD worker. YOUR TICKET: wq#999 "
                  "the service is down and broken, please fix the timer"),
            _line("tool output says crashed", tool_result=True),
        ])
        self.assertEqual(list(il.chat_events("2026-08-01", self.root)), [])

    def test_worktree_project_dirs_skipped(self):
        _write(os.path.join(self.root, "-home-lumen-worktrees-wq999-a1",
                            "aaa11111-0000.jsonl"),
               [_line("LPP is down again?")])
        self.assertEqual(list(il.chat_events("2026-08-01", self.root)), [])

    def test_since_filters_old_messages(self):
        self._interactive()
        got = list(il.chat_events("2026-08-29", self.root))
        self.assertEqual([(g[0], g[1]) for g in got], [("2026-08-29", "fix")])

    def test_missing_root_is_empty(self):
        self.assertEqual(list(il.chat_events("2026-08-01",
                                             "/nonexistent/nope")), [])

    def test_masking(self):
        _write(os.path.join(self.root, "-home-lumen-foreman",
                            "abc12345-0000.jsonl"),
               [_line("token ghp_abcdefghij1234567890 leaked, fix it please")])
        (ev,) = il.chat_events("2026-08-01", self.root)
        self.assertNotIn("ghp_abcdefghij", ev[2])
        self.assertIn("[MASKED]", ev[2])

    def test_collect_chat_idempotent(self):
        self._interactive()
        c = il.db()
        self.assertEqual(il.collect_chat(c, "2026-08-01", self.root), 3)
        self.assertEqual(il.collect_chat(c, "2026-08-01", self.root), 0)
        rows = c.execute("SELECT conf, COUNT(*) FROM events GROUP BY conf"
                         ).fetchall()
        self.assertEqual(rows, [("chat", 3)])


class RollupDedupe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_db = il.LEDGER_DB
        il.LEDGER_DB = os.path.join(self.tmp.name, "ledger.db")

    def tearDown(self):
        il.LEDGER_DB = self._old_db
        self.tmp.cleanup()

    def _ev(self, c, ts, kind, ref, conf):
        c.execute("INSERT INTO events(ts,kind,desc,source_ref,conf,"
                  "undetected_min) VALUES(?,?,?,?,?,0)",
                  (ts, kind, "d", ref, conf))

    def test_chat_suppresses_same_day_regex_only(self):
        c = il.db()
        # history: regex + manual same (kind,day) → both count (W34 baseline
        # must be bit-identical before/after wq#1014)
        self._ev(c, "2026-08-20", "fix", "episode:1", "regex")
        self._ev(c, "2026-08-20", "fix", "manual:1", "manual")
        # new world: regex sighting of an incident the chat miner saw → once
        self._ev(c, "2026-08-27", "detect", "episode:2", "regex")
        self._ev(c, "2026-08-27", "detect", "chat:p:s:2026-08-27", "chat")
        # different kind same day → untouched
        self._ev(c, "2026-08-27", "repeat", "episode:3", "regex")
        c.commit()
        weeks = il.rollup(c)
        self.assertEqual(weeks["2026-W34"]["fix"], 2)
        self.assertEqual(weeks["2026-W35"]["detect"], 1)
        self.assertEqual(weeks["2026-W35"]["repeat"], 1)
        self.assertEqual(weeks["2026-W35"]["events"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
