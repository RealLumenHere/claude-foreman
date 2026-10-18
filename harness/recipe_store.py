#!/usr/bin/env python3
"""recipe_store.py — fleet_memory PostgreSQL backend for the wq#4235 crystallize/freeze
recipes (wq#4288; spec: scratch/research/evolving-agents-dissection.md R2).

Same public API as dispatcher/recipe_crystallizer.py (classify_ticket_type,
lookup_recipe, is_frozen, record_outcome, load_store) and the same cell semantics:
a cell is (ticket_type, model, prompt_template); a win bumps its streak, a loss zeroes
it; a streak of recipe_crystallizer.FREEZE_THRESHOLD freezes the cell as its
ticket_type's recipe; one loss on a frozen cell unfreezes it. While frozen, wins count
toward `wins` but not `streak`, so a frozen row's streak is its streak_at_freeze.

Storage is the `recipes` table in fleet_memory (docker lumen-pgvector, 127.0.0.1:5433),
PK (ticket_type, model, prompt_template), plus a partial unique index allowing one
frozen row per ticket_type. record_outcome() serialises per ticket_type with a
transaction-scoped advisory lock, because the freeze decision reads the other cells of
the same type, not just the row it updates. success_rates() is the per-ticket-type,
per-model view R1 asks for.

Fail-open, two ways:
  - reads (lookup_recipe/is_frozen/load_store/success_rates): PG unreachable, table
    missing, or anything else raising means "nothing frozen" / empty, never an error.
  - record_outcome(): on any PG failure the outcome goes to recipe_crystallizer's JSON
    store AND to an append-only journal next to it (JOURNAL_SUFFIX). Nothing reads JSON
    freezes while PG is the backend, so the fallback cannot pin a route.
After one failed connect, PG is skipped for PG_RETRY_S so a down database costs the
dispatcher one connect timeout per window, not one per ticket.

The journal is what makes "a PG outage drops no counter updates" true rather than
aspirational (wq#4310, after wq#4304 found a live cell orphaned in JSON that PG had
never seen). Writing JSON alone was lossy: the only JSON->PG path is seed_from_json(),
which the operator-run `migrate` calls with ON CONFLICT DO NOTHING, so a fallback
outcome on a cell PG already knows was invisible forever. drain_journal() replays the
journaled outcomes through _record_pg() in recorded order — a replay of the outcome
sequence, not a diff of aggregates, so streaks and freezes land exactly as they would
have live — and record_outcome() calls it before its own PG write, so the first
successful write after an outage heals the gap with no timer to forget to run.
FOREMAN_RECIPE_PG=off is a deliberate JSON backend, not an outage, so it never journals
(journaling it would double-count against a later `migrate` seed of the same cells).

No runtime DDL: the table only comes into being through `recipe_store.py migrate`,
which pg_dumps fleet_memory first and refuses to touch the schema if the dump fails.
Until then every call takes the fail-open path above.

FOREMAN_RECIPE_PG=off forces the JSON path (the test suite's root conftest sets it).
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "dispatcher"))
import recipe_crystallizer as _json_store  # noqa: E402

classify_ticket_type = _json_store.classify_ticket_type
TICKET_TYPES = _json_store.TICKET_TYPES

ENV_FILE = os.environ.get("FM_ENV_FILE", "/home/lumen/agent/.env")
PG_HOST = os.environ.get("FM_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("FM_PORT", "5433"))
PG_DBNAME = os.environ.get("FM_DBNAME", "fleet_memory")
CONNECT_TIMEOUT_S = 3
PG_RETRY_S = 60
BACKUP_DIR = "/home/lumen/backups"
# Journal of outcomes that went to the JSON fallback and still owe PG a replay. Lives
# beside the JSON store (and moves with it, so a test that redirects the store redirects
# the journal). DRAIN_MAX caps one drain so the first close after a long outage pays a
# bounded cost; whatever is left over drains on the next close.
JOURNAL_SUFFIX = ".pending.jsonl"
DRAIN_MAX = 200

_pg_down_until = 0.0


class _PgSkipped(RuntimeError):
    """PG deliberately not tried (FOREMAN_RECIPE_PG=off, or inside the retry window
    after a failed connect). Already logged once, so callers stay quiet about it."""


def _log(msg, exc=None):
    if not isinstance(exc, _PgSkipped):
        print(f"recipe_store: {msg}", file=sys.stderr)


def _table():
    name = os.environ.get("FOREMAN_RECIPE_TABLE", "recipes")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError(f"FOREMAN_RECIPE_TABLE {name!r} is not a plain identifier")
    return name


def _pg_enabled():
    return os.environ.get("FOREMAN_RECIPE_PG", "on") != "off"


def _db_credentials():
    """(user, password) from the DATABASE_URL in agent/.env, read at call time and
    never stored — the minerva/memory/fm_db.py convention."""
    with open(ENV_FILE, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = re.match(r"\s*DATABASE_URL\s*=\s*(.+?)\s*$", line)
            if m:
                parts = urlsplit(m.group(1).strip().strip('"').strip("'"))
                return parts.username, parts.password
    raise RuntimeError(f"DATABASE_URL not found in {ENV_FILE}")


def _connect():
    global _pg_down_until
    if not _pg_enabled():
        raise _PgSkipped("FOREMAN_RECIPE_PG=off")
    if time.time() < _pg_down_until:
        raise _PgSkipped("fleet_memory marked down, retrying later")
    try:
        import psycopg2
        user, password = _db_credentials()
        return psycopg2.connect(
            host=PG_HOST, port=PG_PORT, dbname=PG_DBNAME, user=user, password=password,
            connect_timeout=CONNECT_TIMEOUT_S, options="-c statement_timeout=5000")
    except Exception:
        _pg_down_until = time.time() + PG_RETRY_S
        raise


def _query(sql, params=()):
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def lookup_recipe(ticket_type):
    """{"model", "prompt_template", "streak_at_freeze", "frozen_at"} for the frozen
    cell of this ticket_type, or None — including whenever PG can't answer."""
    if not ticket_type:
        return None
    try:
        rows = _query(
            f"SELECT model, prompt_template, streak, extract(epoch FROM frozen_at) "
            f"FROM {_table()} WHERE ticket_type = %s AND frozen LIMIT 1", (ticket_type,))
    except Exception as e:  # noqa: BLE001 — fail-open by contract
        _log(f"lookup_recipe({ticket_type!r}) fail-open: {e!r}", e)
        return None
    if not rows:
        return None
    model, template, streak, frozen_at = rows[0]
    return {"model": model, "prompt_template": template, "streak_at_freeze": streak,
            "frozen_at": float(frozen_at) if frozen_at is not None else None}


def is_frozen(ticket_type):
    return lookup_recipe(ticket_type) is not None


def load_store():
    """Snapshot in recipe_crystallizer.load_store()'s shape; {"cells": {}, "frozen": {}}
    when PG can't answer."""
    store = {"cells": {}, "frozen": {}}
    try:
        rows = _query(
            f"SELECT ticket_type, model, prompt_template, wins, losses, streak, frozen, "
            f"extract(epoch FROM frozen_at) FROM {_table()} ORDER BY 1, 2, 3")
    except Exception as e:  # noqa: BLE001 — fail-open by contract
        _log(f"load_store fail-open: {e!r}", e)
        return store
    for tt, model, template, wins, losses, streak, frozen, frozen_at in rows:
        store["cells"].setdefault(tt, {})[_json_store._cell_key(model, template)] = {
            "model": model, "prompt_template": template, "streak": streak,
            "successes": wins, "failures": losses}
        if frozen:
            store["frozen"][tt] = {
                "model": model, "prompt_template": template, "streak_at_freeze": streak,
                "frozen_at": float(frozen_at) if frozen_at is not None else None}
    return store


def success_rates(ticket_type=None):
    """Per (ticket_type, model) totals summed over prompt templates, best rate first:
    [{"ticket_type", "model", "wins", "losses", "success_rate"}]. [] when PG can't
    answer."""
    where, params = ("WHERE ticket_type = %s", (ticket_type,)) if ticket_type else ("", ())
    try:
        rows = _query(
            f"SELECT ticket_type, model, sum(wins)::int, sum(losses)::int FROM {_table()} "
            f"{where} GROUP BY 1, 2", params)
    except Exception as e:  # noqa: BLE001 — fail-open by contract
        _log(f"success_rates fail-open: {e!r}", e)
        return []
    out = [{"ticket_type": tt, "model": m, "wins": w, "losses": lo,
            "success_rate": round(w / (w + lo), 3) if w + lo else None}
           for tt, m, w, lo in rows]
    out.sort(key=lambda r: (r["ticket_type"], -(r["success_rate"] or 0), r["model"]))
    return out


def _record_pg(cur, ticket_type, model, prompt_template, success):
    t = _table()
    pk = (ticket_type, model, prompt_template)
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{t}:{ticket_type}",))
    cur.execute(f"INSERT INTO {t} (ticket_type, model, prompt_template) VALUES (%s, %s, %s) "
                f"ON CONFLICT DO NOTHING", pk)
    cur.execute(f"SELECT wins, losses, streak, frozen FROM {t} "
                f"WHERE ticket_type = %s AND model = %s AND prompt_template = %s", pk)
    wins, losses, streak, frozen = cur.fetchone()
    cur.execute(f"SELECT 1 FROM {t} WHERE ticket_type = %s AND frozen", (ticket_type,))
    type_frozen = cur.fetchone() is not None
    freeze_now = False
    if success:
        wins += 1
        if not frozen:
            streak += 1
            freeze_now = streak >= _json_store.FREEZE_THRESHOLD and not type_frozen
    else:
        # wq#4235 step 4: one failure unfreezes immediately — no grace window.
        losses += 1
        streak = 0
        frozen = False
    frozen = frozen or freeze_now
    cur.execute(
        f"UPDATE {t} SET wins = %s, losses = %s, streak = %s, frozen = %s, "
        f"frozen_at = CASE WHEN %s THEN now() WHEN %s THEN frozen_at END, "
        f"last_outcome = %s, updated_at = now() "
        f"WHERE ticket_type = %s AND model = %s AND prompt_template = %s "
        f"RETURNING extract(epoch FROM frozen_at)",
        (wins, losses, streak, frozen, freeze_now, frozen, "win" if success else "loss") + pk)
    (frozen_at,) = cur.fetchone()
    if frozen:
        return {"model": model, "prompt_template": prompt_template,
                "streak_at_freeze": streak, "frozen_at": float(frozen_at)}
    return {"model": model, "prompt_template": prompt_template, "streak": streak,
            "successes": wins, "failures": losses}


# ---- JSON-fallback journal (wq#4310) -----------------------------------------------

def journal_path():
    return os.environ.get("FOREMAN_RECIPE_JOURNAL", _json_store.STORE_PATH + JOURNAL_SUFFIX)


def _journal_append(ticket_type, model, prompt_template, success):
    """Append one owed-to-PG outcome. Never raises: it is called from the fallback path,
    which must stay fail-open even if the journal itself is unwritable."""
    path = journal_path()
    entry = {"ticket_type": ticket_type, "model": model,
             "prompt_template": prompt_template, "success": bool(success), "at": time.time()}
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception as e:  # noqa: BLE001 — a journal failure must not fail the close
        _log(f"journal append failed, outcome stays JSON-only: {e!r}")


def _journal_owes_pg():
    """Cheap hot-path guard: one stat, false when there is nothing to replay."""
    try:
        return os.path.getsize(journal_path()) > 0
    except OSError:
        return False


def _parse_journal(raw):
    """(entries, dropped) — entries in file order, dropped counting lines that are not
    a replayable outcome (truncated write, hand-edit, a type since retired)."""
    entries, dropped = [], 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
            if (e["ticket_type"] in TICKET_TYPES and e["model"] and e["prompt_template"]):
                entries.append(e)
                continue
        except (ValueError, KeyError, TypeError):
            pass
        dropped += 1
    return entries, dropped


def drain_journal(max_entries=DRAIN_MAX):
    """Replay journaled fallback outcomes into PG, oldest first, and drop the ones that
    land. {"replayed", "pending", "dropped"}.

    One transaction per ticket_type, because _record_pg() takes a per-ticket_type
    advisory lock and a transaction that held several could deadlock with a concurrent
    close; one lock per transaction cannot. A group whose transaction fails stays in the
    journal for the next drain, so a replay is never half-counted: _record_pg()'s
    increments and the journal entry's removal commit or roll back together.

    Holds the journal flock throughout, which blocks concurrent appends — those only
    happen while PG is down, which is the one time this function does nothing."""
    if not _journal_owes_pg():
        return {"replayed": 0, "pending": 0, "dropped": 0}
    try:
        fh = open(journal_path(), "r+", encoding="utf-8")
    except OSError as e:
        _log(f"drain_journal cannot open the journal: {e!r}")
        return {"replayed": 0, "pending": 0, "dropped": 0}
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        entries, dropped = _parse_journal(fh.read())
        batch, remainder = entries[:max_entries], entries[max_entries:]
        groups = {}
        for e in batch:
            groups.setdefault(e["ticket_type"], []).append(e)
        replayed, failed = 0, []
        try:
            conn = _connect()
        except Exception as e:  # noqa: BLE001 — still down; everything stays pending
            _log(f"drain_journal deferred, PG unavailable: {e!r}", e)
            conn = None
        if conn is not None:
            try:
                for ticket_type, group in groups.items():
                    try:
                        with conn, conn.cursor() as cur:
                            for e in group:
                                _record_pg(cur, ticket_type, e["model"],
                                           e["prompt_template"], bool(e["success"]))
                        replayed += len(group)
                    except Exception as err:  # noqa: BLE001 — retry this group later
                        _log(f"drain_journal kept {len(group)} {ticket_type} outcomes "
                             f"pending: {err!r}")
                        failed.extend(group)
            finally:
                conn.close()
        else:
            failed = batch
        kept = failed + remainder
        fh.seek(0)
        fh.write("".join(json.dumps(e, sort_keys=True) + "\n" for e in kept))
        fh.truncate()
        fh.flush()
        os.fsync(fh.fileno())
        if replayed or dropped:
            _log(f"drain_journal replayed {replayed}, pending {len(kept)}, dropped {dropped}")
        return {"replayed": replayed, "pending": len(kept), "dropped": dropped}
    finally:
        fh.close()


def record_outcome(ticket_type, model, prompt_template, success):
    """recipe_crystallizer.record_outcome() semantics and return shape, against PG.
    Any PG failure routes the same outcome to the JSON fallback store and the journal.

    Drains the journal first so a healed outage replays in the order it happened: the
    outcomes PG missed count toward this cell's streak before this outcome does."""
    if ticket_type not in TICKET_TYPES or not model or not prompt_template:
        return None
    if _pg_enabled() and _journal_owes_pg():
        drain_journal()
    try:
        conn = _connect()
        try:
            with conn, conn.cursor() as cur:
                return _record_pg(cur, ticket_type, model, prompt_template, bool(success))
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — fail-open by contract
        if _pg_enabled():
            # A real outage: PG owes this outcome, so journal it before the JSON write.
            _log(f"record_outcome falling back to JSON: {e!r}")
            _journal_append(ticket_type, model, prompt_template, success)
        return _json_store.record_outcome(ticket_type, model, prompt_template, success)


# ---- migration (operator CLI; never called on the dispatch/close path) -------------

def _ddl():
    t = _table()
    return (
        f"CREATE TABLE IF NOT EXISTS {t} ("
        f" ticket_type text NOT NULL,"
        f" model text NOT NULL,"
        f" prompt_template text NOT NULL,"
        f" wins integer NOT NULL DEFAULT 0,"
        f" losses integer NOT NULL DEFAULT 0,"
        f" streak integer NOT NULL DEFAULT 0,"
        f" frozen boolean NOT NULL DEFAULT false,"
        f" frozen_at timestamptz,"
        f" last_outcome text CHECK (last_outcome IN ('win', 'loss')),"
        f" updated_at timestamptz NOT NULL DEFAULT now(),"
        f" PRIMARY KEY (ticket_type, model, prompt_template))",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {t}_one_frozen_per_type "
        f"ON {t} (ticket_type) WHERE frozen",
    )


def backup(backup_dir=BACKUP_DIR):
    """pg_dump -Fc fleet_memory into backup_dir; returns the dump path. Raises if the
    dump fails or comes out empty — migrate() runs no DDL without one."""
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        raise RuntimeError("pg_dump not on PATH")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = os.path.join(backup_dir, f"{PG_DBNAME}-pre-wq4288-{stamp}.dump")
    user, password = _db_credentials()
    env = dict(os.environ, PGPASSWORD=password or "")
    proc = subprocess.run(
        [pg_dump, "-h", PG_HOST, "-p", str(PG_PORT), "-U", user, "-d", PG_DBNAME,
         "-Fc", "-f", path], env=env, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not os.path.exists(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"pg_dump failed rc={proc.returncode}: {proc.stderr.strip()[:300]}")
    return path


def seed_from_json(cur, store, threshold):
    """Insert every JSON cell that has no PG row yet (ON CONFLICT DO NOTHING, so a
    re-run never double-counts). A cell arrives frozen only if JSON froze it AND its
    streak meets `threshold` — the PG half of the wq#4288 reconcile. Returns the
    number of rows inserted."""
    t = _table()
    inserted = 0
    for tt, cells in store.get("cells", {}).items():
        frozen = store.get("frozen", {}).get(tt) or {}
        for cell in cells.values():
            is_frozen_cell = (frozen.get("model") == cell["model"]
                              and frozen.get("prompt_template") == cell["prompt_template"]
                              and cell["streak"] >= threshold)
            last = "win" if cell["streak"] > 0 else ("loss" if cell["failures"] else None)
            cur.execute(
                f"INSERT INTO {t} (ticket_type, model, prompt_template, wins, losses, "
                f"streak, frozen, frozen_at, last_outcome) VALUES "
                f"(%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN to_timestamp(%s) END, %s) "
                f"ON CONFLICT DO NOTHING",
                (tt, cell["model"], cell["prompt_template"], cell["successes"],
                 cell["failures"], cell["streak"], is_frozen_cell, is_frozen_cell,
                 frozen.get("frozen_at") or time.time(), last))
            inserted += cur.rowcount
    return inserted


def reconcile_pg(cur, threshold):
    """Unfreeze PG rows frozen below `threshold`, streak untouched. Returns the
    (ticket_type, model, prompt_template) keys released."""
    t = _table()
    cur.execute(f"UPDATE {t} SET frozen = false, frozen_at = NULL, updated_at = now() "
                f"WHERE frozen AND streak < %s "
                f"RETURNING ticket_type, model, prompt_template", (threshold,))
    return [list(r) for r in cur.fetchall()]


def migrate(backup_dir=BACKUP_DIR, skip_backup=False):
    """backup -> additive DDL -> JSON reconcile (under its flock) -> seed PG from JSON
    -> PG reconcile. Idempotent; safe to re-run."""
    threshold = _json_store.FREEZE_THRESHOLD
    report = {"threshold": threshold, "table": _table()}
    report["backup"] = None if skip_backup else backup(backup_dir)
    report["json_released"] = _json_store.reconcile_freezes(threshold)
    # Owed outcomes first: a drained cell then has a PG row, so the seed below leaves it
    # alone (ON CONFLICT DO NOTHING) instead of overwriting the replay with an aggregate.
    report["drained"] = drain_journal(max_entries=10_000)
    store = _json_store.load_store()
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            for stmt in _ddl():
                cur.execute(stmt)
            report["seeded"] = seed_from_json(cur, store, threshold)
            report["pg_released"] = reconcile_pg(cur, threshold)
    finally:
        conn.close()
    return report


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    mig = sub.add_parser("migrate", help="pg_dump, create the recipes table, seed from JSON")
    mig.add_argument("--backup-dir", default=BACKUP_DIR)
    rates = sub.add_parser("rates", help="per-ticket-type success rate by model")
    rates.add_argument("ticket_type", nargs="?")
    sub.add_parser("show", help="print load_store()")
    dr = sub.add_parser("drain", help="replay JSON-fallback outcomes PG still owes")
    dr.add_argument("--max", type=int, default=10_000, help="entries to replay this run")
    dr.add_argument("--seed-missing", action="store_true",
                    help="also insert JSON cells PG has no row for at all — the repair "
                         "for cells orphaned before the journal existed (wq#4310); "
                         "additive, never touches a cell PG already knows")
    args = ap.parse_args(argv)
    if args.cmd == "migrate":
        out = migrate(args.backup_dir)
    elif args.cmd == "rates":
        out = success_rates(args.ticket_type)
    elif args.cmd == "drain":
        out = {"journal": journal_path(), "drained": drain_journal(max_entries=args.max)}
        if args.seed_missing:
            conn = _connect()
            try:
                with conn, conn.cursor() as cur:
                    out["seeded"] = seed_from_json(cur, _json_store.load_store(),
                                                   _json_store.FREEZE_THRESHOLD)
            finally:
                conn.close()
    else:
        out = load_store()
    print(json.dumps(out, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
