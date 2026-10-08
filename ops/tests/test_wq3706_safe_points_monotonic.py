#!/usr/bin/env python3
"""wq#3706 — a cyan-skillfish-governor `[[safe-points]]` table must be non-decreasing in
voltage as frequency rises.

The governor ramps adaptively between the pairs, so a dip means the higher clock runs on
less voltage than the lower one: an undervolt (hang, or silently wrong compute) at the
higher clock, or undefined ramp behaviour at the boundary. wq#3651 shipped exactly that
(1500/875 inserted between 1400/750 and 1600/800); wq#3699 caught it, wq#3706 ruled, this
test keeps it from coming back.

Two kinds of case, deliberately:
  * the live sweep -- every ``[[safe-points]]`` block in every ``ops/**/*.md``, including
    files no one had in mind when the rule was written. This is the case that can fail on
    a doc this ticket never touched.
  * BROKEN_FIXTURE -- the table wq#3651 actually shipped, committed here verbatim so the
    checker is proven able to fail on a pinned, immutable input (wq#2630).

Runs standalone: python3 ops/tests/test_wq3706_safe_points_monotonic.py
Read-only: parses markdown, runs nothing.
"""
import glob
import os
import re
import unittest

OPS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)

# The dip wq#3651 merged, as merged (commit 7b3b5a42, RUNBOOK-ADDENDUM.md).
BROKEN_FIXTURE = """
[[safe-points]]
frequency = 1400
voltage = 750

[[safe-points]]
frequency = 1500
voltage = 875

[[safe-points]]
frequency = 1600
voltage = 800
"""

_POINT = re.compile(
    r"\[\[safe-points\]\]\s*\n\s*frequency\s*=\s*(\d+)\s*\n\s*voltage\s*=\s*(\d+)"
)


def safe_points(text):
    """[(frequency_mhz, voltage_mv)] in the order the document lists them."""
    return [(int(f), int(v)) for f, v in _POINT.findall(text)]


def dips(points):
    """Steps where frequency rises and voltage falls. Empty == table is sane."""
    out = []
    for (f0, v0), (f1, v1) in zip(points, points[1:]):
        if f1 > f0 and v1 < v0:
            out.append(f"{f0}MHz/{v0}mV -> {f1}MHz/{v1}mV")
    return out


class SafePointsMonotonic(unittest.TestCase):
    def test_checker_catches_the_shipped_dip(self):
        points = safe_points(BROKEN_FIXTURE)
        self.assertEqual([(1400, 750), (1500, 875), (1600, 800)], points)
        self.assertEqual(["1500MHz/875mV -> 1600MHz/800mV"], dips(points))

    def test_checker_accepts_the_ruled_table(self):
        ruled = [(500, 700), (1175, 700), (1400, 750), (1500, 900),
                 (1600, 910), (1700, 920), (1800, 930)]
        self.assertEqual([], dips(ruled))

    def test_every_ops_doc_table_is_monotonic(self):
        docs = sorted(glob.glob(os.path.join(OPS, "**", "*.md"), recursive=True))
        self.assertTrue(docs, "no ops docs found -- sweep would pass vacuously")
        checked = 0
        for path in docs:
            with open(path, encoding="utf-8") as fh:
                points = safe_points(fh.read())
            if len(points) < 2:
                continue
            checked += 1
            rel = os.path.relpath(path, OPS)
            self.assertEqual([], dips(points), f"{rel}: voltage dips as frequency rises")
        self.assertTrue(checked, "no ops doc carries a safe-points table any more")

    def test_governor_docs_carry_the_measured_1500_point(self):
        """1500MHz is hapi-1's operating point; 900mV is the measured number for it
        (reports/425_hapi1_bringup_runbook.md:13,79-83). A doc that tables 1500MHz must
        not quietly undervolt it again."""
        for rel in ("wq1337-hapi-bringup/RUNBOOK.md",
                    "wq3651-bc250-bios-flash-governor/RUNBOOK-ADDENDUM.md"):
            with open(os.path.join(OPS, rel), encoding="utf-8") as fh:
                points = safe_points(fh.read())
            at_1500 = {v for f, v in points if f == 1500}
            self.assertEqual({900}, at_1500, f"{rel}: 1500MHz is not at 900mV")


if __name__ == "__main__":
    unittest.main(verbosity=2)
