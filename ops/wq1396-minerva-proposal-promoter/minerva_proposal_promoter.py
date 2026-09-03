#!/usr/bin/env python3
"""minerva_proposal_promoter.py — wq#1396.

Root gap: minerva-classifier/proposal/eval timers grind every 4h and (since
wq#1383) produce GROUNDED proposals, but nothing ever turns an
accepted/deploy_ready proposal into work anyone will do. Proposals sit at
status deploy_ready/staged forever, ticket column empty ('-' in Frank's
description), and the fleet re-discovers the same security debt (e.g. the
Gitea CVE-2026-66877 proposal, Frank had to hand-carry) every cycle.

This is the missing consumer: it reads minerva.db::proposals for rows in
status deploy_ready/staged with no ticket filed yet, files a wq ticket for
each (security/CVE content -> build lane P1, everything else -> build lane
P3, archetype standard-engineering), and writes the new ticket id back to
proposals.ticket so the next run never re-files it.

Idempotent two ways:
  1. proposals.ticket already set -> skipped outright.
  2. proposals.ticket unset but a matching wq ticket already exists (crash
     between `wq add` succeeding and the DB writeback) -> the marker embedded
     in the ticket detail is found by title/detail scan and backfilled
     instead of re-filed. `wq add`'s own title-dedup is a second safety net
     on top of this.

Usage: minerva_proposal_promoter.py [--dry-run] [--limit N]
"""
import argparse
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

MINERVA_DB = "/home/lumen/minerva/minerva.db"
WQ_DB = "/home/lumen/agents/work-queue/queue.db"
WQ_BIN = "/home/lumen/bin/wq"

PROMOTABLE_STATUSES = ("deploy_ready", "staged")

# wq#1396: security/CVE content promotes to P1; everything else is P3.
# Broad on purpose — a missed security proposal costs a lot more than an
# occasional feature proposal filed at P1-adjacent urgency.
SECURITY_RE = re.compile(
    r"\bCVE-\d{4}-\d+\b|vulnerab|exploit|\bRCE\b|remote.code.exec"
    r"|unauthenticated|security patch|security update|security fix"
    r"|privilege escalation|zero.day",
    re.IGNORECASE,
)

ADDED_RE = re.compile(r"added task (\d+)")
DUPLICATE_RE = re.compile(r"DUPLICATE — task (\d+)")

PRI_SECURITY = 1
PRI_FEATURE = 3
LANE = "build"
ARCHETYPE = "standard-engineering"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def ensure_ticket_column(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(proposals)")}
    if "ticket" not in cols:
        conn.execute("ALTER TABLE proposals ADD COLUMN ticket TEXT")
        conn.commit()
        log("migrated: added proposals.ticket column")


def classify_kind(title, proposed_change, current_state):
    blob = " ".join(x or "" for x in (title, proposed_change, current_state))
    return "security" if SECURITY_RE.search(blob) else "feature"


def marker(proposal_id):
    return f"minerva proposal {proposal_id}"


def build_detail(row):
    parts = [row["proposed_change"] or "(no proposed_change on file)"]
    if row["rollback"]:
        parts.append(f"Rollback: {row['rollback']}")
    if row["risk"]:
        parts.append(f"Risk: {row['risk']}")
    if row["source_intel_id"]:
        parts.append(f"Source intel: {row['source_intel_id']}")
    parts.append(f"(auto-filed by minerva-proposal-promoter from {marker(row['id'])}, "
                  f"status={row['status']})")
    return "\n\n".join(parts)


def find_existing_ticket(wq_db, proposal_id):
    """Crash-recovery lookup: a prior run may have filed the ticket and died
    before writing proposals.ticket back. `wq list` truncates title and never
    prints detail, so this reads queue.db directly (read-only, same pattern
    as ops/wq899-pr-recon/pr_recon.py) for the marker this promoter always
    embeds in `detail`."""
    if not os.path.exists(wq_db):
        return None
    needle = marker(proposal_id)
    conn = sqlite3.connect(f"file:{wq_db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id FROM tasks WHERE detail LIKE ? ORDER BY id LIMIT 1",
            (f"%{needle}%",),
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None


def file_ticket(wq_bin, wq_db, row, dry_run):
    kind = classify_kind(row["title"], row["proposed_change"], row["current_state"])
    pri = PRI_SECURITY if kind == "security" else PRI_FEATURE
    detail = build_detail(row)
    args = [
        wq_bin, "add",
        "--lane", LANE,
        "--pri", str(pri),
        "--title", row["title"] or f"(untitled minerva proposal {row['id']})",
        "--detail", detail,
        "--archetype", ARCHETYPE,
    ]
    if dry_run:
        log(f"DRY-RUN would file [{kind} P{pri}]: {row['title']!r}")
        return None
    env = {**os.environ, "WQ_DB": wq_db}
    out = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env)
    added = ADDED_RE.search(out.stdout)
    if added:
        tid = added.group(1)
        log(f"filed wq#{tid} [{kind} P{pri}] for proposal {row['id']}: {row['title']!r}")
        return tid
    dup = DUPLICATE_RE.search(out.stdout)
    if dup:
        tid = dup.group(1)
        log(f"wq#{tid} already covers title {row['title']!r} (wq dedup) — backfilling")
        return tid
    log(f"FAILED to file ticket for proposal {row['id']}: "
        f"rc={out.returncode} stdout={out.stdout.strip()!r} stderr={out.stderr.strip()!r}")
    return None


def promote(conn, wq_bin=WQ_BIN, wq_db=WQ_DB, dry_run=False, limit=None):
    ensure_ticket_column(conn)
    conn.row_factory = sqlite3.Row
    q = (
        "SELECT * FROM proposals WHERE status IN ({}) "
        "AND (ticket IS NULL OR ticket='') "
        "ORDER BY created_at ASC".format(
            ",".join("?" for _ in PROMOTABLE_STATUSES)
        )
    )
    rows = conn.execute(q, PROMOTABLE_STATUSES).fetchall()
    if limit:
        rows = rows[:limit]
    filed, backfilled, failed = 0, 0, 0
    for row in rows:
        tid = find_existing_ticket(wq_db, row["id"]) if not dry_run else None
        if tid:
            backfilled += 1
        else:
            tid = file_ticket(wq_bin, wq_db, row, dry_run)
            if tid:
                filed += 1
            elif not dry_run:
                failed += 1
                continue
        if tid and not dry_run:
            conn.execute("UPDATE proposals SET ticket=? WHERE id=?", (tid, row["id"]))
            conn.commit()
    log(f"done: {len(rows)} candidate(s), filed={filed} backfilled={backfilled} failed={failed}")
    return {"candidates": len(rows), "filed": filed, "backfilled": backfilled, "failed": failed}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--db", default=MINERVA_DB)
    ap.add_argument("--wq-bin", default=WQ_BIN)
    ap.add_argument("--wq-db", default=WQ_DB)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=30)
    try:
        summary = promote(conn, wq_bin=args.wq_bin, wq_db=args.wq_db,
                           dry_run=args.dry_run, limit=args.limit)
    finally:
        conn.close()
    sys.exit(0 if summary["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
