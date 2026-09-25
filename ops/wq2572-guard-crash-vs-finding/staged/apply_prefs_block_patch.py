#!/usr/bin/env python3
"""wq#2572 deploy helper: apply the prefs-block-check crash fix to the live
/home/lumen/minerva/prefs_block.py via exact anchored str.replace. See
ops/wq2572-guard-crash-vs-finding/DESIGN.md and staged/prefs_block.diff (same
change, unified-diff form, for review) -- this script is what actually applies it.
"""
import sys

TARGET = "/home/lumen/minerva/prefs_block.py"

OLD = """def _wq_add(title, detail, wq_bin=WQ_BIN):
    out = subprocess.run(
        [wq_bin, 'add', '--lane', 'user', '--pri', '2', '--title', title,
         '--detail', detail],
        capture_output=True, text=True, check=True).stdout"""

NEW = """def _wq_add(title, detail, wq_bin=WQ_BIN):
    # wq#2572: `wq add` (wq#1676) hard-refuses (exit 6) a user-lane ticket with no
    # `Liveness:` line as an untagged Frank-gate. This drift-notice ticket has no
    # deterministic completion probe -- it is superseded by the NEXT daily check
    # (check_and_file_ticket's own diff-against-last logic), not resolved by a
    # machine-checkable condition -- so it takes the --no-probe escape hatch. This
    # was the actual root cause of prefs-block-check.service's failed-unit state
    # (ISSUE-LOG wq2572): a real crash, not a finding, unrelated to any exit-code
    # convention -- `wq add` started refusing the call and this call site was
    # never updated.
    out = subprocess.run(
        [wq_bin, 'add', '--lane', 'user', '--pri', '2', '--title', title,
         '--detail', detail, '--no-probe',
         'drift-notice ticket, superseded by next daily check, no deterministic probe'],
        capture_output=True, text=True, check=True).stdout"""


def main():
    with open(TARGET) as fh:
        text = fh.read()
    if text.count(OLD) != 1:
        print(f"REFUSING: anchor not found exactly once (found {text.count(OLD)}) -- "
              f"live file has diverged, re-derive the patch", file=sys.stderr)
        sys.exit(1)
    text = text.replace(OLD, NEW)
    with open(TARGET, "w") as fh:
        fh.write(text)
    print(f"patched {TARGET}")


if __name__ == "__main__":
    main()
