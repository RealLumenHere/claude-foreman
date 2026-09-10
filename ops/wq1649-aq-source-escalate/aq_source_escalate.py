#!/usr/bin/env python3
"""aq_source_escalate — aq triage beyond source=probe (wq#1649, F-14).

FLEET_SERVICE_ATLAS.md §5 SPOF #2 (wq#1627): `aq_escalate.py` (bin/aq_escalate.py,
wq#965) only escalates source='probe' CRITICAL rows and the dispatcher-stall title
match. 250 open aq rows at capture time (84 ntfy:lumen-alerts, 78 jay-watch, 37
failed-unit) sit entirely outside that scope; ops/alert_hygiene_audit.py is the only
thing that ever looks at them, and it runs quarterly — far too slow to catch a source
going stale or a guard spamming.

Three rules, run against the same alerts.db aq_escalate.py uses, each with its own
idempotent escalation table (at-most-once ticket per unit/source, same convention as
bin/aq_escalate.py's `escalations` table):

  1. FAILED-UNIT PER-UNIT TICKET: open source='failed-unit' rows, grouped by unit
     (alerts.key), count>=FAILED_UNIT_THRESHOLD -> ONE fleet-fires ticket per unit
     (never one per row — a flapping unit shouldn't flood the queue).
  2. SPAM-SOURCE SUPPRESSION: any source with >=SPAM_THRESHOLD open, non-suppressed
     rows -> one "source is spamming" ticket, then every open row from that source is
     marked owner='aq-spam-suppressed' (stops paging, stops re-counting) until a human
     clears it via the ticket.
  3. FINDING-EXIT AUTOACK: open failed-unit rows whose unit is declared finding-exit
     (ops/wq1657-guard-exit-convention's X-Lumen-Guard=finding-exit contract; source of
     truth here is the same flat allowlist file the wq#1657 classifier reads,
     guard-finding-exit.list) are auto-acked — they are guards working, not failures
     needing triage. Runs first so finding-exit noise never inflates rules 1/2.

Env (tests/extension): AQ_DB, AQ_WQ_BIN, AQ_SRC_ESC_FAILED_UNIT_SOURCE,
AQ_SRC_ESC_FAILED_UNIT_THRESHOLD, AQ_SRC_ESC_SPAM_THRESHOLD,
AQ_SRC_ESC_FINDING_EXIT_LIST.

Intended cadence: same timer family as lumen-aq-escalate (every 10m); not yet wired to
a systemd unit — this ticket is the responder logic, wiring is a follow-up.
"""
import datetime
import os
import re
import sqlite3
import subprocess
import sys

AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
WQ_BIN = os.environ.get("AQ_WQ_BIN", "/home/lumen/bin/wq")
LANE = "fleet-fires"

FAILED_UNIT_SOURCE = os.environ.get("AQ_SRC_ESC_FAILED_UNIT_SOURCE", "failed-unit")
FAILED_UNIT_THRESHOLD = int(os.environ.get("AQ_SRC_ESC_FAILED_UNIT_THRESHOLD", "3"))
SPAM_THRESHOLD = int(os.environ.get("AQ_SRC_ESC_SPAM_THRESHOLD", "50"))
FINDING_EXIT_LIST = os.environ.get(
    "AQ_SRC_ESC_FINDING_EXIT_LIST", "/home/lumen/bin/guard-finding-exit.list")
SUPPRESS_OWNER = "aq-spam-suppressed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS unit_escalations(
  unit TEXT PRIMARY KEY,
  ticket_id INTEGER,
  count_at_escalation INTEGER,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_escalations(
  source TEXT PRIMARY KEY,
  ticket_id INTEGER,
  open_count_at_escalation INTEGER,
  created_at TEXT NOT NULL);
"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db():
    c = sqlite3.connect(AQ_DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def load_finding_exit_units(path=None):
    path = path or FINDING_EXIT_LIST
    try:
        with open(path) as f:
            return {line.strip() for line in f if line.strip() and not line.startswith("#")}
    except FileNotFoundError:
        return set()


def _wq_add(title, detail):
    cmd = [WQ_BIN, "add", "--lane", LANE, "--pri", "0", "--title", title, "--detail", detail]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ), timeout=30)
    except Exception as e:
        print(f"  wq add EXC for {title!r}: {e}")
        return None
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"added task (\d+)", out)
    if m:
        return int(m.group(1))
    m = re.search(r"DUPLICATE .* task (\d+)", out)  # open ticket with this title already exists
    if m:
        return int(m.group(1))
    print(f"  wq add FAILED for {title!r}: {out.strip()[:200]}")
    return None


def escalate_failed_units(c):
    """Rule 1: one ticket per unit, not per row."""
    rows = c.execute(
        "SELECT key, COUNT(*) n, MAX(last_ts) last_ts, MIN(first_ts) first_ts, "
        "GROUP_CONCAT(id) ids FROM alerts WHERE source=? AND status='open' "
        "GROUP BY key HAVING COUNT(*)>=?",
        (FAILED_UNIT_SOURCE, FAILED_UNIT_THRESHOLD)).fetchall()
    n_ticket = 0
    for r in rows:
        unit = r["key"]
        if c.execute("SELECT 1 FROM unit_escalations WHERE unit=?", (unit,)).fetchone():
            continue
        title = f"[fleet-fires] failed-unit spam: {unit} ({r['n']} open aq rows)"
        detail = (
            f"Auto-escalated by aq_source_escalate (wq#1649 failed-unit rule).\n\n"
            f"Unit: {unit}\nOpen aq rows: {r['n']} (aq ids: {r['ids']})\n"
            f"First seen: {r['first_ts']}   Last: {r['last_ts']}\n\n"
            f"RUNBOOK: systemctl status {unit}; journalctl -u {unit} -n 50; if this is a "
            f"finding-exit guard working as designed, add it to {FINDING_EXIT_LIST} "
            f"(wq#1657 contract) so future runs auto-ack it instead of ticketing it.\n\n"
            f"Acceptance: unit no longer failing (or declared finding-exit), open rows "
            f"resolved, this ticket closed.\n"
        )
        ticket_id = _wq_add(title, detail)
        if ticket_id is None:
            continue  # transient failure: retry next run
        c.execute(
            "INSERT OR REPLACE INTO unit_escalations(unit,ticket_id,count_at_escalation,created_at) "
            "VALUES(?,?,?,?)", (unit, ticket_id, r["n"], now()))
        c.execute(
            "UPDATE alerts SET owner='aq-escalation', note=COALESCE(note,'')||? "
            "WHERE source=? AND key=? AND status='open'",
            (f" [escalated {now()} -> fleet-fires wq#{ticket_id}]", FAILED_UNIT_SOURCE, unit))
        c.commit()
        n_ticket += 1
    return n_ticket


def escalate_spam_sources(c):
    """Rule 2: any source with >=SPAM_THRESHOLD open rows -> one ticket + suppress."""
    rows = c.execute(
        "SELECT source, COUNT(*) n FROM alerts WHERE status='open' "
        "AND (owner IS NULL OR owner != ?) GROUP BY source HAVING COUNT(*)>=?",
        (SUPPRESS_OWNER, SPAM_THRESHOLD)).fetchall()
    n_ticket = 0
    for r in rows:
        source = r["source"]
        if c.execute("SELECT 1 FROM source_escalations WHERE source=?", (source,)).fetchone():
            continue
        title = f"[fleet-fires] source is spamming: {source} ({r['n']} open aq rows)"
        detail = (
            f"Auto-escalated by aq_source_escalate (wq#1649 spam-source rule).\n\n"
            f"Source: {source}\nOpen aq rows: {r['n']} (>= {SPAM_THRESHOLD} threshold)\n\n"
            f"All open rows from this source have been auto-suppressed (owner="
            f"{SUPPRESS_OWNER!r}) so they stop paging and stop counting toward future spam "
            f"checks. Triage the backlog, then either fix the source or clear suppression "
            f"by resetting owner on the rows that are actually resolved.\n\n"
            f"Acceptance: source's open-row count back under {SPAM_THRESHOLD} (fixed or "
            f"deliberately demoted), this ticket closed.\n"
        )
        ticket_id = _wq_add(title, detail)
        if ticket_id is None:
            continue
        c.execute(
            "INSERT OR REPLACE INTO source_escalations"
            "(source,ticket_id,open_count_at_escalation,created_at) VALUES(?,?,?,?)",
            (source, ticket_id, r["n"], now()))
        c.execute(
            "UPDATE alerts SET owner=?, note=COALESCE(note,'')||? "
            "WHERE source=? AND status='open'",
            (SUPPRESS_OWNER, f" [auto-suppressed {now()} -> fleet-fires wq#{ticket_id}]", source))
        c.commit()
        n_ticket += 1
    return n_ticket


def autoack_finding_exit(c):
    """Rule 3: open failed-unit rows whose unit is a declared finding-exit guard get
    auto-acked, not routed for triage. Runs before rules 1/2 so this noise never
    inflates a per-unit or spam-source ticket."""
    units = load_finding_exit_units()
    n = 0
    for unit in units:
        cur = c.execute(
            "UPDATE alerts SET status='acked', owner='aq-escalation', "
            "note=COALESCE(note,'')||? WHERE source=? AND key=? AND status='open'",
            (f" [auto-acked {now()}: finding-exit guard, wq#1657 contract]",
             FAILED_UNIT_SOURCE, unit))
        n += cur.rowcount
    if units:
        c.commit()
    return n


def run():
    c = db()
    n_acked = autoack_finding_exit(c)
    n_unit_tickets = escalate_failed_units(c)
    n_source_tickets = escalate_spam_sources(c)
    c.close()
    print(f"aq_source_escalate: {n_acked} row(s) auto-acked (finding-exit), "
          f"{n_unit_tickets} failed-unit ticket(s), {n_source_tickets} spam-source ticket(s)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
