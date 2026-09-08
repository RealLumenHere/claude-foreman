#!/usr/bin/env python3
"""uncharge_attempt.py — wq#1499: correct an attempt-N.json fworker already wrote for
a ticket where the classifier mis-scored a harness fault (no real model work ran) as
a charged attempt.

self_heal.attempt_count() and runaway.stuck_ticket_ids() both count every filename
matching self_heal.ATTEMPT_FILE_RE (`^attempt-(\\d+)\\.json$`) under
_runs/wq<id>/ — neither can tell a genuine attempt from a harness-fault one after the
fact, and "Do NOT hand-edit _runs" rules out fixing a bad record by hand. This tool
renames the file to attempt-N.uncharged.json instead: the `$`-anchored regex no longer
matches it, so both counters silently skip it from here on, while the original content
(plus an appended uncharged_reason/uncharged_at) stays on disk as the audit trail —
never deleted, never rewritten in place.

By default a record is only renamed if it matches the wq#1499 harness-fault signature
fworker/harness/classifier.py now also detects live (turns<=1, elapsed_s<10, a nonzero
exit code, and a classification from the pre-fix fallback buckets a thin/no-diff result
frame used to land in: CRASH, LAUNCH_FAILURE, INDETERMINATE) — pass --force to override
that guard for a record whose evidence doesn't match.

CLI:
  uncharge_attempt.py <ticket_id> --attempt N --reason "..."
  uncharge_attempt.py <ticket_id> --all --reason "..."     # every matching attempt-N.json
  add --dry-run to preview, --force to skip the fault-signature guard
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import self_heal  # noqa: E402 — reuses RUNS_ROOT/ATTEMPT_FILE_RE, the same source of
                   # truth attempt_count()/stuck_ticket_ids() read.

FAULT_CLASSIFICATIONS = {"CRASH", "LAUNCH_FAILURE", "INDETERMINATE"}
MAX_FAULT_ELAPSED_S = 10
MAX_FAULT_TURNS = 1


def looks_like_harness_fault(rec):
    """wq869/attempt-5.json shape (also #879, #983, #1122, #1350): every field here
    read straight off the persisted attempt record, never inferred."""
    if rec.get("classification") not in FAULT_CLASSIFICATIONS:
        return False
    turns = rec.get("turns")
    if turns is None or turns > MAX_FAULT_TURNS:
        return False
    elapsed = rec.get("elapsed_s")
    if elapsed is None or elapsed >= MAX_FAULT_ELAPSED_S:
        return False
    if rec.get("exit_code") in (None, 0):
        return False
    return True


def run_dir(tid):
    return os.path.join(self_heal.RUNS_ROOT, f"wq{tid}")


def all_attempt_numbers(tid):
    try:
        names = os.listdir(run_dir(tid))
    except OSError:
        return []
    out = []
    for name in names:
        m = self_heal.ATTEMPT_FILE_RE.match(name)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def uncharge_one(tid, n, reason, force, dry_run):
    d = run_dir(tid)
    src = os.path.join(d, f"attempt-{n}.json")
    dst = os.path.join(d, f"attempt-{n}.uncharged.json")
    if not os.path.isfile(src):
        return False, f"no such record: {src}"
    with open(src) as f:
        rec = json.load(f)
    if not force and not looks_like_harness_fault(rec):
        return False, (
            f"attempt-{n}.json does not match the wq#1499 harness-fault signature "
            f"(classification={rec.get('classification')} turns={rec.get('turns')} "
            f"elapsed_s={rec.get('elapsed_s')} exit_code={rec.get('exit_code')}) — "
            f"pass --force to uncharge anyway")
    if dry_run:
        return True, f"would rename {src} -> {dst}"
    rec["uncharged_reason"] = reason
    rec["uncharged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = dst + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2, default=str)
    os.rename(tmp, dst)
    os.remove(src)
    return True, f"{src} -> {dst}"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticket_id", type=int)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--attempt", type=int, help="uncharge this one attempt number")
    g.add_argument("--all", action="store_true",
                   help="uncharge every attempt-N.json matching the fault signature")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--force", action="store_true",
                     help="uncharge even if the record doesn't match the fault signature")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    targets = ([args.attempt] if args.attempt is not None
               else all_attempt_numbers(args.ticket_id))
    if not targets:
        print(f"no attempt-N.json records found for wq{args.ticket_id}", file=sys.stderr)
        return 1

    exit_code = 0
    any_ok = False
    for n in targets:
        ok, msg = uncharge_one(args.ticket_id, n, args.reason, args.force, args.dry_run)
        print(("OK   " if ok else "SKIP ") + msg)
        any_ok = any_ok or ok
        if not ok and args.attempt is not None:
            exit_code = 1
    if args.all and not any_ok:
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
