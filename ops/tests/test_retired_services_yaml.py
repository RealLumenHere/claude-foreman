#!/usr/bin/env python3
"""wq#2039 — docs/fleet/retired_services.yaml must actually parse as YAML.

Runs standalone: python3 ops/tests/test_retired_services_yaml.py
Read-only: parses the committed file, touches nothing live.

Why this exists: the file's own header says retirement-audit tooling (wq#1644)
"should read this file before flagging a guard target as stale", but it had
never been valid YAML — a guards entry carried an unquoted `: ` inside a
multi-line plain scalar, so safe_load raised "mapping values are not allowed
here" at every revision up to and including dafd943. A hand-maintained record
nothing can parse is prose wearing a .yaml extension; this test keeps it
honest, because the only consumer that would have caught the breakage does not
exist yet.
"""
import os
import sys
import unittest

import yaml

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir)
PATH = os.path.join(REPO, "docs", "fleet", "retired_services.yaml")


class RetiredServicesYamlTest(unittest.TestCase):
    def setUp(self):
        with open(PATH) as f:
            self.doc = yaml.safe_load(f)

    def test_parses_as_a_list_of_entries(self):
        self.assertIsInstance(self.doc, list)
        self.assertTrue(self.doc, "file parsed but is empty")

    def test_every_entry_has_the_documented_fields(self):
        # Fields per the file header. `units` is the one other tooling keys on.
        for entry in self.doc:
            self.assertIsInstance(entry, dict, f"not a mapping: {entry!r}")
            for field in ("name", "units", "retired_on", "reason"):
                self.assertIn(field, entry, f"{entry.get('name')!r} missing {field}")
            self.assertIsInstance(entry["units"], list, entry["name"])

    def test_unit_names_are_not_accidentally_nested(self):
        # A `- name:` line at the wrong indent silently folds one entry into
        # the previous one's last block scalar; catching it here is cheaper
        # than noticing a retirement record went missing.
        names = [e["name"] for e in self.doc]
        self.assertEqual(len(names), len(set(names)), f"duplicate names: {names}")

    def test_signal_engine_entry_is_present_and_records_apply_status(self):
        entry = next((e for e in self.doc if e["name"] == "signal-engine"), None)
        self.assertIsNotNone(entry, "wq#2039 signal-engine entry missing")
        self.assertEqual(
            sorted(entry["units"]),
            ["lumen-signal-engine.service", "lumen-signal-engine.timer"],
        )
        # The record is a decision, not a host state, until the apply lands.
        guard_keys = [k for g in entry["guards"] if isinstance(g, dict) for k in g]
        self.assertTrue(
            any(k.startswith("apply status") for k in guard_keys),
            f"no apply-status guard row: {guard_keys}",
        )

    def test_lpp_entry_records_the_mask_and_its_ticket(self):
        # wq#2218: lumen-pokemon-live was unmasked in good faith because no
        # record said why it was masked (wq#2073).
        entry = next((e for e in self.doc if e["name"].startswith("lpp")), None)
        self.assertIsNotNone(entry, "wq#2218 LPP entry missing")
        self.assertIn("lumen-pokemon-live.service", entry["units"])
        self.assertEqual(entry["load_state"], "masked")
        self.assertEqual(entry["retired_on"], "2026-09-13")
        self.assertIn("wq#2073", entry["ticket"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
