#!/usr/bin/env python3
"""model_alias_sweep.py — wq#1442 one-off: canonicalize or clear model aliases on
open/blocked tickets that predate wq add/edit --model validating against
research/models.yaml (wq#1436/wq#1434 evidence: a ticket's model column held the
raw alias 'opus-5', fworker handed it straight to `claude --model`, and the CLI
404'd with model_not_found — two charged attempts, blocked UNKNOWN:CRASH).

Every OTHER open/blocked ticket whose model column is a bare alias (or the same
kind of typo/stale id) is one dispatch away from the identical failure. This
sweep finds them ahead of time:
  - model already a full id in research/models.yaml         -> left alone
  - model is a known alias/shorthand (router.ALIASES)        -> canonicalized
  - model matches neither                                    -> cleared ('')
    (nothing sane to canonicalize a made-up/typo'd id to; clearing lets the
    router assign a real tuple at next dispatch instead of 404ing again)

Read-only enumeration hits WQ_DB directly (dispatcher/self_heal.py's own
convention for ticket bookkeeping); every write goes through the real `wq edit`
CLI so it re-validates through the same wq#1442 norm_tuple() path a human
operator would — this script never writes the tasks table itself.

Rollback: `wq edit <id> --model <original> --force` restores the exact prior
string (find it in this run's printed 'was <model>' log line) if a canonicalize
or clear turns out to be wrong for a specific ticket.
"""
import argparse
import os
import sqlite3
import subprocess
import sys

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
ORCH_DIR = os.path.join(os.path.dirname(HARNESS_DIR), "orchestrator")
sys.path.insert(0, ORCH_DIR)
import router  # noqa: E402

WQ_BIN = os.environ.get("MODEL_ALIAS_SWEEP_WQ", "/home/lumen/bin/wq")
WQ_DB = os.environ.get("WQ_DB", "/home/lumen/agents/work-queue/queue.db")


def resolve(model, ids, aliases):
    """Classify one stored model string against the manifest's id/alias sets
    (router.Manifest.models keys / router.ALIASES — wq#1329's single source of
    truth). Returns (action, new_model): action in {"keep", "canonicalize",
    "clear"}; new_model is the canonical id for "canonicalize", else None."""
    if model in ids:
        return "keep", model
    if model in aliases:
        return "canonicalize", aliases[model]
    return "clear", None


def sweep(db_path=None, wq_bin=None, ids=None, aliases=None, dry_run=False):
    """Enumerate open/blocked tickets with a non-empty model, resolve each against
    (ids, aliases) (defaults to the live research/models.yaml manifest), and
    (unless dry_run) apply via `wq edit`. Returns a list of per-ticket dicts."""
    db_path = db_path or WQ_DB
    wq_bin = wq_bin or WQ_BIN
    if ids is None or aliases is None:
        manifest = router.load_manifest()
        ids = set(manifest.models.keys())
        aliases = router.ALIASES

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, model FROM tasks WHERE status IN ('open','blocked') "
        "AND model IS NOT NULL AND model != ''").fetchall()
    conn.close()

    results = []
    for tid, model in rows:
        action, new_model = resolve(model, ids, aliases)
        entry = {"id": tid, "model": model, "action": action, "new_model": new_model}
        if action == "keep":
            results.append(entry)
            continue
        value = new_model if action == "canonicalize" else ""
        if dry_run:
            entry["applied"] = "dry_run"
        else:
            p = subprocess.run([wq_bin, "edit", str(tid), "--model", value],
                                capture_output=True, text=True, timeout=30)
            entry["applied"] = p.returncode == 0
            if p.returncode != 0:
                entry["wq_error"] = (p.stdout + p.stderr).strip()
        results.append(entry)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="write changes via `wq edit` (default: dry-run report only)")
    ap.add_argument("--db", default=None, help="override WQ_DB path")
    a = ap.parse_args(argv)

    results = sweep(db_path=a.db, dry_run=not a.apply)
    changed = [r for r in results if r["action"] != "keep"]
    for r in changed:
        if r["applied"] == "dry_run":
            tag = "[dry run — would apply]"
        elif r["applied"] is True:
            tag = "[applied]"
        else:
            tag = f"[FAILED: {r.get('wq_error', '')}]"
        target = repr(r["new_model"]) if r["action"] == "canonicalize" else "(cleared)"
        print(f"wq#{r['id']}: {r['action']} {r['model']!r} -> {target} {tag}")

    failed = any(r["applied"] is False for r in changed)
    if not changed:
        print("no tickets needed canonicalization")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
