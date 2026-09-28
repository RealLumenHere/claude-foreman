#!/usr/bin/env python3
"""wq#3018 acceptance probe — the orphan-prefix backstop, measured ON MAIN.

wq#3007 CONTRADICTED wq#2922: the fix was sound but never merged, and the gate that
closed the ticket (proof_type=citation) never checked `main`. So this probe asks the
two questions that gate could not, and asks them where it matters:

  1  CLOSED — the class wq#2922 was filed against is shut in the module as installed:
     reports/wq2868_probe_underfloor_class.py section B at 0 SILENT, run against the
     masking.py on the import path rather than one extracted from a worker's branch.

  2  QUIET — a fail-LOUD backstop's own failure mode is the opposite one: alarming on
     text that holds no credential. wq#2638 spent a whole ticket teaching this guard
     to stay quiet behind an already-masked value, and `_orphan_matches()` re-opens
     exactly that door. So sweep a corpus drawn from OUTSIDE the population the fix
     was designed around (wq#2630) — real committed repo text, prose and code, none
     of it written for this class, read at a PINNED rev so the run is repeatable —
     and require the backstop to find nothing in any of it. Section B alone could
     not have failed this way: every one of its 96 cases is a shape built to trip.

  3  MIRRORED — the daemon keeps a hand copy off this import path, and a fix that
     lands in one copy only is the same landing gap one layer down. Same class, same
     bar, through token_engine_daemon.mask_hits().

Run from the repo root.  Prints counts and file:hit ids only, never a body.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "harness"))
sys.path.insert(0, HERE)
import masking  # noqa: E402
import wq2868_probe_underfloor_class as probe  # noqa: E402

# The revs the corpus is read at.  A live working-tree read is not evidence: the files
# drift under the next commit and the probe stops being re-runnable (wq#3018's own
# ticket text — an unpinned probe input is not re-runnable evidence).  FOREMAN_REV is
# the main tip wq#3018's branch was cut from; KB is its own repo (the ISSUE-LOG read
# every ticket owes, and the one corpus file from outside this repo entirely), so it
# carries its own pin rather than a HEAD that moves under the next worker's append.
FOREMAN_REV = "e58f9b99"
KB_REPO, KB_REV = "/home/lumen/kb", "26e286f"

# Chosen for the shapes that stress a fail-loud prefix backstop, not for coverage:
# masking's own tests and reports (which write `<prefix>MASKED` constantly, the exact
# residue wq#2638 taught the guard to ignore), the mask mirrors, and ordinary prose.
CORPUS = [
    "harness/masking.py",
    "harness/tests/test_masking.py",
    "harness/tests/test_mask_foreign_prefix_matrix.py",
    "harness/tests/test_mask_mirror_coverage.py",
    "harness/tests/test_mask_triage.py",
    "token-engine/token_engine_daemon.py",
    "ops/wq2316-mask-secrets-boundary/mask-secrets",
    "reports/wq2868_judgment_wq2848_floor_fallback.md",
    "reports/wq2923_verify_wq2868_foreign_prefix.md",
    "reports/wq3007_verify_wq2922_underfloor_unmerged.md",
    "kb:ISSUE-LOG.md",
    "README.md",
    "BUILD_PLAN.md",
    "LUMEN_PUBLIC_RUNBOOK.md",
]


def _at_rev(path):
    """The file's committed bytes at its pin, or None if the corpus entry is gone."""
    repo, rev = ROOT, FOREMAN_REV
    if path.startswith("kb:"):
        repo, rev, path = KB_REPO, KB_REV, path[len("kb:"):]
    r = subprocess.run(["git", "-C", repo, "show", rev + ":" + path],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def closed():
    """1 — section B on the masking.py actually on the import path."""
    name, total, fails = probe.section_b()
    return name + " [on main]", total, fails


def quiet():
    """2 — the backstop finds nothing in real committed text."""
    fails, total = [], 0
    for path in CORPUS:
        text = _at_rev(path)
        if text is None:
            fails.append(f"{path}/MISSING@pin")     # a corpus that shrank silently
            continue                                  # is a probe that stopped probing
        total += 1
        for label, _prefix, value in masking._orphan_matches(text):
            fails.append(f"{path}/{label}/len={len(value)}")
    return "orphan backstop quiet on real committed text", total, fails


def mirrored():
    """3 — the daemon's hand copy closes the same class."""
    sys.path.insert(0, os.path.join(ROOT, "token-engine"))
    sys.path.insert(0, os.path.join(ROOT, "harness"))
    import fworker  # noqa: F401  (side effect: fleet_exec_policy on sys.path)
    import token_engine_daemon as ted

    fails, total = [], 0
    for label, pre, cs, floor, _open, fill in probe.RULES:
        if not all(c in cs for c in masking.MASK):
            continue
        blen = floor + 14
        for off in range(0, floor):
            body = fill * off + masking.MASK + fill * (blen - len(masking.MASK) - off)
            total += 1
            if not ted.mask_hits("x " + pre + body + " y"):
                fails.append(f"{label}/off{off}/SILENT")
    return "daemon mirror closes the same class", total, fails


def main():
    print("masking.py", masking.MASK, "rules", len(masking.PATTERNS),
          "orphan shapes", len(masking._ORPHAN_SHAPES),
          "corpus revs", FOREMAN_REV, "+ kb", KB_REV)
    bad = 0
    for fn in (closed, quiet, mirrored):
        name, total, fails = fn()
        assert total, f"{name}: probe generated no cases — it cannot fail (wq#2630)"
        print(f"{name}: {total} cases, {len(fails)} failing")
        for row in fails[:8]:
            print("   ", row)
        if len(fails) > 8:
            print(f"    ... and {len(fails) - 8} more")
        bad += len(fails)
    print("TOTAL FAILING", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
