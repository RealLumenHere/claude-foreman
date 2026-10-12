#!/usr/bin/env python3
"""aq_daily_ack_extend — extends the daily aq auto-ack rule (wq#3176, [audit parent
wq#3162]).

622 open aq rows at audit time, dominated by Failed/Recovered twins that never get
paired off and by recurring info-class sources the existing daily auto-ack
(/home/lumen/agents/alert-queue/aq_autoack.py) skips because it only touches
count=1 (fired-once) rows. Live snapshot at authoring time: 91 open `failed-unit`
rows, 79 of them count>1 -- the existing rule structurally cannot reach them, so
they accumulate forever once a unit stops flapping. Three rules against the same
alerts.db aq.py/aq_autoack.py use:

  1. RECOVERED/FAILED PAIR ACK: `source='failed-unit'` posts two independent open
     rows keyed `Failed unit: <unit>` / `Recovered: <unit>` (never a single row that
     transitions to resolved -- see ops/r4_undetected_sensors.py's `_token_for`
     doc). When the `Recovered: <unit>` row's last_ts is newer than the matching
     `Failed unit: <unit>` row's last_ts, the failure is superseded: ack both.
  2. STALE INFO-CLASS ACK (recurring included): same info tier and 7-day staleness
     window as aq_autoack.py's existing rule, but without the count=1 restriction
     -- last_ts age already guarantees the row hasn't fired recently, so a
     recurring default-priority row that stopped firing 7+ days ago is just as
     stale as a one-shot.
  3. PER-SOURCE DISPLAY CAP: any source with more open rows than the cap keeps
     only its N most-recently-active rows open; older overflow is auto-acked.
     Catches sources like `jay-watch` that are neither info-class nor a
     Failed/Recovered pair but still dominate `aq list` with duplicate history.

All three ack (never resolve/delete) with an audit note, same idempotent
convention as aq_autoack.py and ops/wq1649-aq-source-escalate. Never touches
high/urgent priority (4,5,high) -- those are the real rows this ticket exists to
surface.

Host (wq#3737): lumen-aq-autoack.timer, the original host, was disabled in wq#3648's
timer cut, and after that nothing closed an aq row. dispatch_health.py's 5-minute
check (lumen-foreman-health.timer, one of the timers the cut kept) now calls
`--daily`, which runs at most once per 24h and records the outcome in STATE_PATH.
sentinel-watch reads that file and raises a finding when hygiene fails or stops.
`--dry-run` prints the counts and writes nothing. Its cap count is an upper bound
because it does not see the stale-rule acks that would land first.

Env (tests/extension): AQ_DB, AQ_DAILY_ACK_AFTER_DAYS, AQ_DAILY_ACK_PER_SOURCE_CAP,
AQ_DAILY_ACK_FAILED_UNIT_SOURCE, AQ_DAILY_ACK_STATE.
"""
import datetime
import json
import os
import sqlite3
import sys
import time

AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
STATE_PATH = os.environ.get("AQ_DAILY_ACK_STATE", "/home/lumen/logs/aq-daily-ack.state.json")
DAILY_EVERY = 24 * 3600
FAILED_UNIT_SOURCE = os.environ.get("AQ_DAILY_ACK_FAILED_UNIT_SOURCE", "failed-unit")
ACK_AFTER_DAYS = int(os.environ.get("AQ_DAILY_ACK_AFTER_DAYS", "7"))
PER_SOURCE_CAP = int(os.environ.get("AQ_DAILY_ACK_PER_SOURCE_CAP", "15"))

# Same info tier as aq_autoack.py's existing rule -- never high/urgent (4,5,high).
INFO_TIER = ("min", "low", "default", "1", "2", "3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_ts TEXT NOT NULL, last_ts TEXT NOT NULL,
  source TEXT NOT NULL, key TEXT NOT NULL,
  title TEXT, message TEXT, priority TEXT DEFAULT 'default',
  count INTEGER DEFAULT 1, status TEXT DEFAULT 'open',
  owner TEXT DEFAULT 'unassigned', note TEXT, resolved_ts TEXT);
"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db():
    c = sqlite3.connect(AQ_DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


DRY_RUN = False


def _ack_ids(c, ids, reason):
    if not ids or DRY_RUN:
        return len(ids)
    stamp = now()
    qmarks = ",".join("?" * len(ids))
    c.execute(
        f"UPDATE alerts SET status='acked', owner='auto-hygiene', "
        f"note=COALESCE(note,'')||? WHERE id IN ({qmarks})",
        (f" [auto-ack {stamp}: {reason}, wq#3176]", *ids))
    c.commit()
    return len(ids)


def ack_recovered_failed_pairs(c):
    """Rule 1: a `Recovered: <unit>` row newer than its `Failed unit: <unit>`
    twin means the unit is back up -- ack both halves of the pair."""
    recs = c.execute(
        "SELECT id, key, last_ts FROM alerts WHERE source=? AND status='open' "
        "AND key LIKE 'Recovered: %'", (FAILED_UNIT_SOURCE,)).fetchall()
    n = 0
    for rec in recs:
        unit = rec["key"][len("Recovered: "):]
        fail = c.execute(
            "SELECT id, last_ts FROM alerts WHERE source=? AND key=? AND status='open'",
            (FAILED_UNIT_SOURCE, f"Failed unit: {unit}")).fetchone()
        if not fail:
            continue  # no open twin to pair with -- leave the lone row alone
        if rec["last_ts"] <= fail["last_ts"]:
            continue  # failure is still the more recent event -- unit not actually recovered
        n += _ack_ids(c, [rec["id"], fail["id"]], f"recovered newer than failed ({unit})")
    return n


def ack_stale_info_class(c, days=None):
    """Rule 2: same info tier + staleness window as aq_autoack.py, minus the
    count=1 restriction -- a recurring row that stopped firing `days` ago is
    exactly as stale as a one-shot; last_ts age is what proves it stopped."""
    days = ACK_AFTER_DAYS if days is None else days
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ph = ",".join("?" * len(INFO_TIER))
    rows = c.execute(
        f"SELECT id FROM alerts WHERE status='open' AND last_ts<? AND priority IN ({ph}) "
        "AND title NOT LIKE '[deploy_ready]%' AND title NOT LIKE '[blocked]%' "
        "AND title NOT LIKE '[defer]%'",
        (cutoff, *INFO_TIER)).fetchall()
    return _ack_ids(c, [r["id"] for r in rows], f"stale info-class >{days}d")


def cap_per_source(c, cap=None):
    """Rule 3: keep only the `cap` most-recently-active open rows per source;
    ack the older overflow so one noisy source can't bury the real rows."""
    cap = PER_SOURCE_CAP if cap is None else cap
    sources = [r["source"] for r in c.execute(
        "SELECT source FROM alerts WHERE status='open' GROUP BY source "
        "HAVING COUNT(*)>?", (cap,)).fetchall()]
    n = 0
    for source in sources:
        rows = c.execute(
            "SELECT id FROM alerts WHERE source=? AND status='open' "
            "ORDER BY last_ts DESC", (source,)).fetchall()
        overflow = [r["id"] for r in rows[cap:]]
        n += _ack_ids(c, overflow, f"per-source display cap ({cap}) on {source}")
    return n


def run(dry_run=False):
    global DRY_RUN
    DRY_RUN = dry_run
    c = db()
    n_pairs = ack_recovered_failed_pairs(c)
    n_stale = ack_stale_info_class(c)
    n_capped = cap_per_source(c)
    c.close()
    verb = "would ack" if dry_run else "acked"
    print(f"aq_daily_ack_extend: {n_pairs} row(s) {verb} (recovered/failed pairs), "
          f"{n_stale} row(s) {verb} (stale info-class incl. recurring), "
          f"{n_capped} row(s) {verb} (per-source display cap)")
    return 0


def run_daily(now=None):
    """Run once STATE_PATH's last success is DAILY_EVERY old. Record last_ok, or err +
    err_ts, so a reader (sentinel-watch) can tell broken or stopped hygiene from quiet."""
    now = time.time() if now is None else now
    try:
        with open(STATE_PATH) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    if "last_ok" in st and now - st["last_ok"] < DAILY_EVERY:
        return 0
    rc = 0
    try:
        run()
        st["last_ok"] = now
        st.pop("err", None)
    except Exception as e:  # noqa: BLE001 -- recorded for the reader, not swallowed
        st.update(err=repr(e)[:300], err_ts=now)
        print(f"aq_daily_ack_extend: FAILED {e!r}", file=sys.stderr)
        rc = 1
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, STATE_PATH)
    return rc


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(run_daily() if "--daily" in args else run(dry_run="--dry-run" in args))
