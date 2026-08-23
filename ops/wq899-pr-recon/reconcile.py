#!/usr/bin/env python3
"""reconcile.py — apply decisions.tsv.

Reads the decision record and executes the `close` rows through pr_act.py: comment the
stale-ref note, then close. `land` and `reopen` rows are recorded for the audit trail but
NOT executed here — a merge is not something a table should do behind a reviewer's back.

Idempotent: pr_act refuses to close a PR that is already closed or merged, so re-running
after a partial failure is safe and reports "skip".

    python3 ops/wq899-pr-recon/reconcile.py --dry-run
    python3 ops/wq899-pr-recon/reconcile.py
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def rows():
    with open(os.path.join(HERE, "decisions.tsv")) as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 4:
                sys.exit(f"decisions.tsv:{n}: expected 4 tab-separated fields, got {len(parts)}")
            yield parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    counts = {}
    for repo, pr, action, note in rows():
        counts[action] = counts.get(action, 0) + 1
        if action not in ("close", "comment", "reopen"):
            print(f"{action:<6} {repo}#{pr} (recorded, not executed here)")
            continue
        # A `reopen` row leaves the PR open on purpose — the note is what stops the next
        # audit re-flagging it as an unexplained orphan, so it still has to be posted.
        verb = "close" if action == "close" else "comment"
        if a.dry_run:
            print(f"would {verb} {repo}#{pr}: {note[:66]}...")
            continue
        r = subprocess.run([sys.executable, os.path.join(HERE, "pr_act.py"), verb,
                            repo, pr, "--note", note], capture_output=True, text=True)
        print((r.stdout + r.stderr).strip() or f"{repo}#{pr}: no output")
        if r.returncode != 0:
            sys.exit(f"stopping: {repo}#{pr} failed, nothing after it was attempted")
    print("\n" + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
