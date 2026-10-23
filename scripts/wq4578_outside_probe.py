#!/usr/bin/env python3
"""wq#4578 falsifiable acceptance probe for _STATE_LINE's greedy value group.

Per wq#2630: the fix's design population is this bundle's own fixtures, so the
probe draws its inputs from OUTSIDE that population — real fleet prose that
already uses the "(since YYYY-MM-DD)" idiom, pulled rev-pinned out of two
other documents (one in another repo) so the probe stays re-runnable:

  * /home/lumen/kb @ 9e3d59a3 : ISSUE-LOG.md
  * this repo    @ b2d4b925  : reports/2686_remediate_wadjet_remediation_hold_role.md

Each pinned sentence is wrapped as a lumen_state.py `current` line — value =
the prose, then the real trailing "(since DATE)  [tag]" the tool appends — and
parsed with the shipped parse_state_lines(). The probe FAILS (exit 1) if any
value is truncated at an embedded since-clause or any `since` is not the
trailing date; it fails on the pre-wq#4578 non-greedy pattern.

Both shipped copies of the parser are probed: the wq#4553 phase-1 bundle and
the wq#4554 phase-2 bundle, which is the one actually installed into
/home/lumen/minerva/memory. Probing only phase 1 would have passed while the
live renderer stayed broken.

Run: python3 scripts/wq4578_outside_probe.py
"""
import importlib.util
import os
import re
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BUNDLES = [
    os.path.join(REPO, "ops", "wq4553-context-builder-live-roster"),
    os.path.join(REPO, "ops", "wq4554-decisions-migration"),
]
SINCE_IDIOM = re.compile(r"\(since [^)]*\)")
PINS = [
    ("/home/lumen/kb", "9e3d59a3", "ISSUE-LOG.md"),
    (REPO, "b2d4b925", "reports/2686_remediate_wadjet_remediation_hold_role.md"),
]
TRAILING_DATE = "2026-10-23"
TAG = "  [UNVERIFIABLE \u2014 no probe]"


def load_module(bundle):
    spec = importlib.util.spec_from_file_location(
        "cb_wq4578_probe_" + os.path.basename(bundle).replace("-", "_"),
        os.path.join(bundle, "context_builder.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fm_db"] = types.SimpleNamespace(connect=lambda: None)
    spec.loader.exec_module(mod)
    return mod


def pinned_prose():
    """Every line of pinned fleet prose that carries a parenthetical since."""
    out = []
    for repo, sha, path in PINS:
        text = subprocess.run(
            ["git", "-C", repo, "show", "%s:%s" % (sha, path)],
            capture_output=True, text=True, check=True).stdout
        for line in text.splitlines():
            if SINCE_IDIOM.search(line):
                out.append((path, " ".join(line.split())[:160]))
    return out


def check(bundle, prose):
    """Returns the number of pinned values this bundle's parser got wrong."""
    mod = load_module(bundle)
    name = os.path.basename(bundle)
    bad = 0
    for path, value in prose:
        line = "  probehost.status         = %s  (since %s)%s" % (
            value, TRAILING_DATE, TAG)
        facts = mod.parse_state_lines(line)
        if len(facts) != 1:
            print("FAIL %s %s: %d facts parsed from one line"
                  % (name, path, len(facts)))
            bad += 1
            continue
        got = facts[0]
        if got["since"] != TRAILING_DATE or got["value"] != value:
            print("FAIL %s %s:\n  since=%r (want %r)\n  value=%r\n  want =%r"
                  % (name, path, got["since"], TRAILING_DATE,
                     got["value"], value))
            bad += 1
    print("%s: %d/%d pinned prose values parsed with the trailing since-clause"
          % (name, len(prose) - bad, len(prose)))
    return bad


def main():
    prose = pinned_prose()
    if not prose:
        print("FAIL: no pinned prose with a parenthetical since-clause found")
        return 1
    bad = sum(check(bundle, prose) for bundle in BUNDLES)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
