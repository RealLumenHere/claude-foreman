#!/usr/bin/env python3
"""phoenix_disk_guard.py — wq#1299 bounded-retention backstop for the Phoenix
tracer's SQLite store.

Two independent guardrails, because relying on either alone is fragile:

1. Retention purge: delete spans/traces older than RETENTION_DAYS directly
   from the SQLite file. Phoenix versions may also ship a native
   project-level retention setting (VERIFY against the running image's
   docs/UI) — this script is the enforced backstop regardless of whether
   that native setting exists or is configured correctly.
2. Disk alert: page via ntfy if the db file exceeds MAX_DB_BYTES even after
   a purge attempt, edge-triggered (only on breach->clear/clear->breach
   transitions) like litellm-auth-alert (wq#911) so it doesn't spam.

Purge is schema-defensive: it only deletes from a table if that table and
its configured timestamp column actually exist (checked via PRAGMA
table_info). If Phoenix's schema doesn't match TABLE_TIMESTAMP_COLUMNS, the
purge step no-ops and logs a warning instead of guessing at DELETE targets —
a silent no-op is safe, a wrong DELETE is not.

Usage:
  phoenix_disk_guard.py check [--db PATH] [--retention-days N]
                               [--max-bytes N] [--state PATH] [--dry-run]
"""
import argparse
import json
import os
import sqlite3
import sys
import time

DEFAULT_DB = "/home/lumen/var/phoenix/phoenix.db"
DEFAULT_RETENTION_DAYS = 14
DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
DEFAULT_STATE = "/home/lumen/foreman/ops/wq1299-phoenix-tracer/disk_guard_state.json"
NTFY_BIN = "/home/lumen/bin/ntfy-send"

# table -> timestamp column, in the shape Arize Phoenix's SQLite schema is
# documented to use (VERIFY against `sqlite3 phoenix.db .schema` on first
# deploy — see README). Values are epoch seconds or ISO8601; both handled.
TABLE_TIMESTAMP_COLUMNS = {
    "spans": "start_time",
    "traces": "start_time",
}


def table_columns(conn, table):
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {r[1] for r in rows}


def purge_old_rows(db_path, retention_days, dry_run=False):
    """Returns dict of table -> rows deleted (or would-delete under dry_run).
    No-ops (0, logged) for any table/column pair that doesn't exist."""
    cutoff_epoch = time.time() - retention_days * 86400
    result = {}
    if not os.path.exists(db_path):
        return result
    conn = sqlite3.connect(db_path)
    try:
        for table, col in TABLE_TIMESTAMP_COLUMNS.items():
            cols = table_columns(conn, table)
            if not cols or col not in cols:
                result[table] = None  # schema mismatch, skipped
                continue
            query = (
                f"SELECT COUNT(*) FROM {table} WHERE "
                f"(typeof({col})='text' AND {col} < datetime({cutoff_epoch}, 'unixepoch')) "
                f"OR (typeof({col})!='text' AND {col} < {cutoff_epoch})"
            )
            count = conn.execute(query).fetchone()[0]
            if count and not dry_run:
                conn.execute(query.replace("SELECT COUNT(*)", "DELETE", 1))
                conn.commit()
            result[table] = count
        if not dry_run:
            conn.execute("VACUUM")
    finally:
        conn.close()
    return result


def db_size_bytes(db_path):
    total = 0
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            total += os.path.getsize(p)
    return total


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"breached": False}


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f)


def ntfy(title, message, priority="default"):
    if not os.path.exists(NTFY_BIN):
        print(f"[ntfy skipped, no binary] {title}: {message}", file=sys.stderr)
        return
    os.system(
        f'{NTFY_BIN} --title {json.dumps(title)} --priority {priority} '
        f'--message {json.dumps(message)}'
    )


def check(db_path, retention_days, max_bytes, state_path, dry_run=False):
    purge_result = purge_old_rows(db_path, retention_days, dry_run=dry_run)
    skipped = [t for t, v in purge_result.items() if v is None]
    if skipped:
        print(f"WARNING: schema mismatch, purge skipped for: {skipped}", file=sys.stderr)

    size = db_size_bytes(db_path)
    state = load_state(state_path)
    was_breached = state.get("breached", False)
    is_breached = size > max_bytes

    if is_breached and not was_breached:
        ntfy(
            "phoenix-disk-guard",
            f"Phoenix SQLite store at {size / 1e9:.2f} GiB, exceeds "
            f"{max_bytes / 1e9:.2f} GiB cap even after purge attempt "
            f"(retention={retention_days}d). {db_path}",
            priority="high",
        )
    elif was_breached and not is_breached:
        ntfy("phoenix-disk-guard", f"Phoenix SQLite store back under cap ({size / 1e9:.2f} GiB).")

    if not dry_run:
        save_state(state_path, {"breached": is_breached})

    print(json.dumps({
        "db_size_bytes": size,
        "max_bytes": max_bytes,
        "breached": is_breached,
        "purged": purge_result,
    }))
    return 0


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--db", default=DEFAULT_DB)
    c.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    c.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    c.add_argument("--state", default=DEFAULT_STATE)
    c.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.cmd == "check":
        return check(args.db, args.retention_days, args.max_bytes, args.state, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
