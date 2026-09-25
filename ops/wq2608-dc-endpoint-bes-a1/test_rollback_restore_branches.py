#!/usr/bin/env python3
"""Functional test for rollback.sh's two restore blocks, without root or systemd.
Run: python3 test_rollback_restore_branches.py

rollback.sh can only be exercised for real by root on Bes, and this worker has no
ssh(bes) grant -- so wq#2608 shipped it unexercised and wq#2646's verify pass found
its DC_UNIT restore block had its if/else bodies swapped (ISSUE-LOG #766). This
test is the exercise that was missing: it extracts each `if [ -e "${VAR}.pre-wq2608.bak" ]`
block verbatim from rollback.sh, runs it against throwaway files in a temp dir, and
asserts the resulting on-disk state for both of the two reachable backup states.

It runs twice over: the pre-fix blob pinned at commit 2681073 (which MUST still
mishandle both DC_UNIT states -- that is the regression witness proving this test
can actually detect the bug) and the current working-tree file (which must handle
all four cases). A test that only checked the fixed file could not fail.

The two blocks are deliberately NOT symmetric, so "mirror the CFD_CONFIG block" is
the wrong fix for DC_UNIT:

  DC_UNIT     apply.sh's backup() only snapshots a path that already existed, and
              apply.sh installs the unit unconditionally. No backup therefore means
              the unit is net-new, and rollback must delete it.
  CFD_CONFIG  cloudflared_ingress_patch.py always writes the backup (config.yml must
              already exist for cloudflared to be running), so its no-backup state is
              unreachable in practice. Leaving the live config alone and telling the
              operator to hand-edit is correct there -- deleting an operator's
              cloudflared config would be far worse than a lingering ingress entry.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROLLBACK = HERE / "rollback.sh"
# The bug wq#2646 found, pinned so the regression witness cannot drift.
PRE_FIX_COMMIT = "26810738301d8ee9a281e61b272605f66795fc39"

RESTORED = "restored-from-backup"
REMOVED = "file-absent"
LEFT = "live-left-in-place"

EXPECTED = {
    ("DC_UNIT", True): RESTORED,
    ("DC_UNIT", False): REMOVED,
    ("CFD_CONFIG", True): RESTORED,
    ("CFD_CONFIG", False): LEFT,
}


def pre_fix_source():
    return subprocess.run(
        ["git", "-C", str(HERE), "show",
         f"{PRE_FIX_COMMIT}:ops/wq2608-dc-endpoint-bes-a1/rollback.sh"],
        capture_output=True, text=True, check=True,
    ).stdout


def extract_block(source, var):
    """Pull the `if [ -e "${VAR}.pre-wq2608.bak" ] ... fi` block verbatim."""
    m = re.search(
        r'^if \[ -e "\$\{' + var + r'\}\.pre-wq2608\.bak" \].*?^fi$',
        source, re.M | re.S,
    )
    assert m, f"could not locate the {var} restore block"
    return m.group(0)


def run_state(block, var, backup_exists):
    """Run one restore block against throwaway paths; return the on-disk outcome."""
    tmp = Path(tempfile.mkdtemp(prefix="wq2608-rollback-"))
    try:
        live = tmp / "live.conf"
        live.write_text("LIVE-installed-by-apply\n")
        if backup_exists:
            (tmp / "live.conf.pre-wq2608.bak").write_text("ORIGINAL-pre-wq2608\n")

        proc = subprocess.run(
            ["bash", "-c", f'set -euo pipefail\n{var}="{live}"\n{block}\n'],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr

        if not live.exists():
            return REMOVED
        if live.read_text() == "ORIGINAL-pre-wq2608\n":
            return RESTORED
        return LEFT
    finally:
        shutil.rmtree(tmp)


def check(label, source, want_correct):
    """Run all four cases against one revision of the script."""
    bad = []
    for var in ("DC_UNIT", "CFD_CONFIG"):
        block = extract_block(source, var)
        for backup_exists in (True, False):
            got = run_state(block, var, backup_exists)
            want = EXPECTED[(var, backup_exists)]
            state = "backup exists" if backup_exists else "no backup "
            ok = got == want
            print(f"  {var:10} {state} -> {got:20} (want {want:20}) "
                  f"{'ok' if ok else 'MISHANDLED'}")
            if not ok:
                bad.append((var, state))

    if want_correct:
        if bad:
            print(f"FAIL: {label} mishandles {bad}")
            return False
        return True

    # Regression witness: the pinned pre-fix blob must still show exactly the two
    # DC_UNIT failures wq#2646 reported, and no CFD_CONFIG failure.
    if sorted(v for v, _ in bad) != ["DC_UNIT", "DC_UNIT"]:
        print(f"FAIL: {label} was expected to mishandle both DC_UNIT states and "
              f"neither CFD_CONFIG state; got {bad}. The test harness, not the "
              f"script, is probably wrong.")
        return False
    return True


print(f"pre-fix blob @ {PRE_FIX_COMMIT[:7]} (regression witness -- "
      f"DC_UNIT must fail both states):")
witness_ok = check("pre-fix blob", pre_fix_source(), want_correct=False)

print(f"\ncurrent {ROLLBACK.name} (must handle all four):")
current_ok = check("current rollback.sh", ROLLBACK.read_text(), want_correct=True)

if witness_ok and current_ok:
    print("\nPASS: the fix handles both DC_UNIT states, the CFD_CONFIG block is "
          "unchanged and correct, and the test still catches the original bug.")
    sys.exit(0)
sys.exit(1)
