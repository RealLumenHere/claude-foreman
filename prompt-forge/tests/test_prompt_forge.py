#!/usr/bin/env python3
"""Tests for Prompt Forge (wq#957): state parsing, assembly, JSON extraction,
and an end-to-end HTTP smoke test against a live server on an ephemeral port.
Stdlib unittest; no network beyond loopback; no LLM (key resolution is
pointed at a nonexistent file so llm.available() is False and every path
exercises the deterministic degradation)."""

import json
import os
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["PROMPT_FORGE_KEY_FILE"] = "/nonexistent/prompt-forge.key"
os.environ.pop("LITELLM_API_KEY", None)

import fleet_context
import llm
import templates

STATE_SAMPLE = """\
 3dprinter.role           = 3D print server (Klipper dual-instance)  (since 2026-06-09)
 3dprinter.tailscale_ip   = 100.114.82.85  (since 2026-06-09)
    anubis.role           = Borg backup repo + Gitea  (since 2026-06-01)
    anubis.tailscale_ip   = 100.121.17.5  (since 2026-06-01)
    anubis.lan_ip         = 192.168.8.232  (since 2026-06-01)
     fleet.autonomy_level = FULL AUTONOMY 2026-08-22 (Frank, explicit). Never Thor.  (since 2026-08-22)
   no_role.tailscale_ip   = 100.1.2.3  (since 2026-06-01)
garbage line without shape
"""


class TestStateParsing(unittest.TestCase):
    def test_parse_shapes(self):
        facts = fleet_context.parse_state_lines(STATE_SAMPLE)
        self.assertEqual(len(facts), 7)
        self.assertEqual(facts[0]["subject"], "3dprinter")
        self.assertEqual(facts[1]["value"], "100.114.82.85")
        self.assertEqual(facts[5]["since"], "2026-08-22")

    def test_roster_and_autonomy_from_sample(self):
        fleet_context._cache.clear()
        fleet_context._cache["state"] = (
            float("inf"), fleet_context.parse_state_lines(STATE_SAMPLE))
        roster = fleet_context.roster()
        self.assertEqual([n["node"] for n in roster], ["3dprinter", "anubis"])
        self.assertEqual(roster[1]["lan_ip"], "192.168.8.232")
        auto = fleet_context.autonomy()
        self.assertEqual(len(auto), 1)
        self.assertIn("FULL AUTONOMY", auto[0]["value"])
        fleet_context._cache.clear()

    def test_bad_slug_rejected(self):
        for bad in ("../etc", "a/b", "", "x" * 65):
            self.assertFalse(fleet_context.project_context(bad)["found"])


class TestTemplates(unittest.TestCase):
    def setUp(self):
        fleet_context._cache.clear()
        fleet_context._cache["state"] = (
            float("inf"), fleet_context.parse_state_lines(STATE_SAMPLE))

    def tearDown(self):
        fleet_context._cache.clear()

    def test_assemble_contains_live_facts_and_rules(self):
        p = templates.assemble(
            "Reverse engineer a Google Glass device",
            category="project",
            filters=["hardware", "sensitive"],
            answers=[{"q": "Which generation?", "a": "Explorer XE-C"}],
            assumptions=["Assume bootloader locked"],
        )
        for needle in (
            "100.114.82.85", "FULL AUTONOMY",           # measured, not recalled
            "wq whoami --register reverse-engineer-a-google",
            "Loop-break", "No backup, no write",         # category + filter rules
            "OUT of the orchestrator",                   # sensitive filter
            "A: Explorer XE-C", "Assume bootloader locked",
        ):
            self.assertIn(needle, p)
        self.assertNotIn("Briefing", p)  # no framing when LLM absent

    def test_unknown_category_and_filters_ignored(self):
        p = templates.assemble("x", category="nonsense", filters=["bogus"])
        self.assertIn("Category: project", p)

    def test_state_down_degrades_with_label(self):
        fleet_context._cache["state"] = (float("inf"), [])
        p = templates.assemble("x", category="audit")
        self.assertIn("UNAVAILABLE", p)
        self.assertIn("NO standing", p)
        self.assertIn("You verify; you do not fix", p)


class TestLLMHelpers(unittest.TestCase):
    def test_unavailable_without_key(self):
        self.assertFalse(llm.available())
        self.assertIsNone(llm.chat("s", "u"))

    def test_extract_json(self):
        self.assertEqual(
            llm.extract_json('noise ```json\n{"a": {"b": 1}}\n``` tail'),
            {"a": {"b": 1}})
        self.assertIsNone(llm.extract_json("no json here"))
        self.assertIsNone(llm.extract_json('[1,2]'))
        self.assertIsNone(llm.extract_json('{"broken": '))


class TestHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        os.environ["PROMPT_FORGE_STATE_DIR"] = cls.tmp.name
        import server
        server.STATE_DIR = Path(cls.tmp.name)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, r.read()

    def _post(self, path, obj):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_ui_and_health(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Prompt Forge", body)
        self.assertEqual(json.loads(self._get("/api/health")[1]),
                         {"ok": True, "llm": False})

    def test_meta_lists_categories(self):
        meta = json.loads(self._get("/api/meta")[1])
        self.assertIn("audit", meta["categories"])
        self.assertIn("continuation", meta["filters"])

    def test_clarify_degrades_without_llm(self):
        status, obj = self._post("/api/clarify", {"description": "RE a router"})
        self.assertEqual(status, 200)
        self.assertEqual(obj["questions"], [])
        self.assertFalse(obj["llm"])

    def test_generate_and_history(self):
        status, entry = self._post("/api/generate", {
            "description": "Audit the backup chain", "category": "audit"})
        self.assertEqual(status, 200)
        self.assertIn("## Bootstrap", entry["prompt"])
        self.assertFalse(entry["llm_framing"])
        hist = json.loads(self._get("/api/history")[1])
        self.assertEqual(hist[0]["id"], entry["id"])

    def test_validation(self):
        self.assertEqual(self._post("/api/generate", {})[0], 400)
        self.assertEqual(self._post("/api/nope", {"description": "x"})[0], 404)


if __name__ == "__main__":
    unittest.main()
