#!/usr/bin/env python3
"""R4 ledger — two automatic sensors closing caveat 3 (wq#963, wq#1015).

wq#963  probe_unticketed_recovery — a product/service went DOWN then
        RECOVERED with no wq ticket referencing the incident anywhere in
        the window. Answers "did an intervention/recovery happen without a
        paper trail". Logged as kind='fix' (counts toward the R4 headline,
        same bucket a human FRANK-FIX marker would land in).

wq#1015 probe_undetected_downtime — a product's own forward-progress signal
        (per instincts PRODUCT-LIVENESS / playbooks/fleet-audit.md) shows a
        stall gap with NO aq alert anywhere near onset. Answers "did the
        fleet go down without anything noticing". Logged as kind='downtime'
        (undetected_min only; does NOT count toward the headline, matching
        existing convention e.g. ledger event id 36 "LPP dead 12h").

Both write directly into intervention_ledger.db's `events` table (conf=
'sensor') so lumen-intervention-ledger.timer's next `run` picks them up in
rollup()/emit() same as any other row -- no schema change, no forked store
(R4 spec + wq#1014's ticket both say: build on the existing ledger).

NO DOUBLE-COUNTING: the two sensors answer different questions (ticket
coverage vs alert coverage) and write different `kind` values into
different source_ref namespaces (aq:<id> vs lppgap:<iso>). A single real
incident can legitimately appear in both, one, or neither -- that is
correct, not a bug: "alerted but never ticketed" and "never alerted at
all" are both real and distinct gaps. rollup() only sums kind IN
(fix,detect,repeat) into the headline, so a downtime-only row never
inflates the intervention count, and re-running either sensor is
idempotent via the ledger's own UNIQUE(ts,kind,source_ref).

Env overrides (test isolation, same convention as aq.py's AQ_DB and
sentinel_probes.py's SENTINEL_WQ_DB / SENTINEL_NTFY_UPDATES_CAPTURE):
  R4_LEDGER_DB        default /home/lumen/foreman/ops/intervention_ledger.db
  AQ_DB               default /home/lumen/agents/alert-queue/alerts.db
  R4_WQ_DB            default /home/lumen/agents/work-queue/queue.db
  R4_POKEMON_EVENTS   default /home/lumen/pokemon/events
  R4_NTFY_CAPTURE     file path -> append-only JSON capture instead of network
"""
import datetime, glob, json, os, re, sqlite3, sys, urllib.request

D = os.path.dirname(os.path.abspath(__file__))
LEDGER_DB = os.environ.get("R4_LEDGER_DB", os.path.join(D, "intervention_ledger.db"))
AQ_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
WQ_DB = os.environ.get("R4_WQ_DB", "/home/lumen/agents/work-queue/queue.db")
POKEMON_EVENTS = os.environ.get("R4_POKEMON_EVENTS", "/home/lumen/pokemon/events")
NTFY_UPDATES = os.environ.get("SENTINEL_NTFY_UPDATES_URL", "http://localhost:8080/lumen-updates")
NTFY_CAPTURE = os.environ.get("R4_NTFY_CAPTURE")

# Self-referential aq keys these sensors post their own summaries under --
# excluded from unticketed_recovery so the sensors never flag themselves.
SELF_KEYS = {"probe-r4-unticketed-recovery", "probe-r4-undetected-downtime"}

# Per-product forward-progress providers (playbooks/fleet-audit.md §2).
# Each entry: stall threshold minutes, aq source/key(s) considered "coverage".
PRODUCTS = {
    "lpp": {
        "stall_min": 45,  # matches POKEMON_STALL_CRIT_S in sentinel_probes.py
        "aq_probe_keys": {"probe-pokemon-liveness"},
        "aq_unit_names": {"lumen-pokemon-live.service"},
    },
}


def ntfy_digest(title, msg):
    if NTFY_CAPTURE:
        try:
            with open(NTFY_CAPTURE, "a") as fh:
                fh.write(json.dumps({"ts": _now_iso(), "title": title, "msg": msg}) + "\n")
        except Exception as e:
            print(f"ntfy_digest capture failed: {e}")
        return
    try:
        req = urllib.request.Request(NTFY_UPDATES, data=msg.encode(),
                                      headers={"Title": title, "Priority": "default"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print(f"ntfy_digest failed: {e}")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _parse_ts(s):
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.datetime.fromisoformat(s[:10]).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            return None


def ledger_conn():
    c = sqlite3.connect(LEDGER_DB, timeout=15)
    c.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
        desc TEXT, source_ref TEXT, conf TEXT DEFAULT 'regex',
        undetected_min INTEGER DEFAULT 0,
        UNIQUE(ts, kind, source_ref))""")
    return c


def ledger_insert(c, ts, kind, desc, source_ref, undetected_min=0):
    """INSERT OR IGNORE, sensor confidence. Returns True if a NEW row landed."""
    before = c.total_changes
    c.execute("INSERT OR IGNORE INTO events"
              "(ts,kind,desc,source_ref,conf,undetected_min) VALUES(?,?,?,?, 'sensor', ?)",
              (ts, kind, desc[:300], source_ref, undetected_min))
    return c.total_changes > before


def _aq_ro():
    c = sqlite3.connect(f"file:{AQ_DB}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _wq_ro():
    c = sqlite3.connect(f"file:{WQ_DB}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _has_ticket(token, window_start, window_end):
    """wq#963: was ANY ticket created in the window whose title/detail
    mentions the alert's key/unit (case-insensitive substring)?"""
    if not token:
        return False
    like = f"%{token.lower()}%"
    try:
        with _wq_ro() as q:
            row = q.execute(
                "SELECT COUNT(*) n FROM tasks WHERE created_at BETWEEN ? AND ? "
                "AND (LOWER(title) LIKE ? OR LOWER(detail) LIKE ?)",
                (window_start, window_end, like, like)).fetchone()
            return (row["n"] or 0) > 0
    except sqlite3.Error as e:
        print(f"wq ticket-coverage query failed (fail-open=ticketed): {e}")
        return True  # fail open: an unreadable wq store must never inflate the metric


def _escalation_ticket(aq_id):
    try:
        with _aq_ro() as a:
            row = a.execute("SELECT ticket_id FROM escalations WHERE aq_id=?",
                             (aq_id,)).fetchone()
            return row["ticket_id"] if row else None
    except sqlite3.Error:
        return None


def _token_for(source, key, title):
    """Best search token for ticket-coverage matching. Strips the
    'Failed unit: '/'Recovered: ' prefixes lumen-failed-units uses so the
    same unit name is matched regardless of which half of the pair we're on."""
    t = key or title or ""
    t = re.sub(r"^(Failed unit|Recovered):\s*", "", t, flags=re.I)
    t = re.sub(r"\.service$", "", t)
    return t.strip()


def probe_unticketed_recovery(lookback_hours=48, min_duration_min=5,
                               ticket_grace_hours=24):
    """wq#963. Returns (new_count, findings[]) for testability; the
    sentinel_probes.py wrapper adapts this to the (ok,detail,critical) shape."""
    now = datetime.datetime.now(datetime.timezone.utc)
    since = (now - datetime.timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = ledger_conn()
    findings = []

    # Part A: sentinel-probe rows with clean edge-clear (status='resolved').
    try:
        with _aq_ro() as a:
            rows = a.execute(
                "SELECT id, first_ts, resolved_ts, source, key, title FROM alerts "
                "WHERE status='resolved' AND resolved_ts >= ? AND source='probe'",
                (since,)).fetchall()
    except sqlite3.Error as e:
        return 0, [f"aq query failed: {e}"]
    for r in rows:
        if r["key"] in SELF_KEYS:
            continue
        t0, t1 = _parse_ts(r["first_ts"]), _parse_ts(r["resolved_ts"])
        if not t0 or not t1:
            continue
        dur_min = (t1 - t0).total_seconds() / 60
        if dur_min < min_duration_min:
            continue
        if _escalation_ticket(r["id"]):
            continue
        token = _token_for(r["source"], r["key"], r["title"])
        win_start = (t0 - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        win_end = (t1 + datetime.timedelta(hours=ticket_grace_hours)).strftime("%Y-%m-%d %H:%M:%S")
        if _has_ticket(token, win_start, win_end):
            continue
        desc = f"{r['source']}/{r['key']}: recovered after {dur_min:.0f}min, no wq ticket found"
        if ledger_insert(c, r["resolved_ts"][:10], "fix", desc, f"aq:{r['id']}"):
            findings.append(desc)

    # Part B: failed-unit Failed/Recovered pairs (never reach status=resolved
    # -- lumen-failed-units posts two independent open rows keyed by prefix).
    try:
        with _aq_ro() as a:
            recs = a.execute(
                "SELECT id, first_ts, key FROM alerts WHERE source='failed-unit' "
                "AND key LIKE 'Recovered: %' AND first_ts >= ?", (since,)).fetchall()
    except sqlite3.Error as e:
        return len(findings), findings + [f"aq failed-unit query failed: {e}"]
    for rec in recs:
        unit = rec["key"][len("Recovered: "):]
        try:
            with _aq_ro() as a:
                fail = a.execute(
                    "SELECT id, first_ts FROM alerts WHERE source='failed-unit' "
                    "AND key=? AND first_ts < ? ORDER BY first_ts DESC LIMIT 1",
                    (f"Failed unit: {unit}", rec["first_ts"])).fetchone()
        except sqlite3.Error:
            continue
        if not fail:
            continue  # recovery with no matching prior failure in range -- skip, not evidence
        t0, t1 = _parse_ts(fail["first_ts"]), _parse_ts(rec["first_ts"])
        if not t0 or not t1:
            continue
        dur_min = (t1 - t0).total_seconds() / 60
        if dur_min < min_duration_min:
            continue
        token = _token_for("failed-unit", unit, unit)
        win_start = (t0 - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        win_end = (t1 + datetime.timedelta(hours=ticket_grace_hours)).strftime("%Y-%m-%d %H:%M:%S")
        if _has_ticket(token, win_start, win_end):
            continue
        desc = f"failed-unit/{unit}: recovered after {dur_min:.0f}min, no wq ticket found"
        if ledger_insert(c, rec["first_ts"][:10], "fix", desc, f"aq:{rec['id']}"):
            findings.append(desc)

    c.commit()
    return len(findings), findings


def _lpp_progress_timestamps(lookback_hours):
    """All turn_summary event timestamps across live-agent-*.jsonl files
    touched within the lookback window (files roll; read every file whose
    mtime falls in-window plus one extra to catch a boundary-crossing gap)."""
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    cutoff = now - lookback_hours * 3600 - 3600  # 1h pad for boundary gaps
    ts = []
    try:
        files = sorted(glob.glob(os.path.join(POKEMON_EVENTS, "live-agent-*.jsonl")),
                        key=lambda f: os.path.getmtime(f))
    except OSError:
        return ts
    for f in files:
        try:
            if os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        try:
            with open(f, errors="replace") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if e.get("kind") == "turn_summary" and e.get("t"):
                        dt = _parse_ts(e["t"])
                        if dt:
                            ts.append(dt)
        except OSError:
            continue
    return sorted(ts)


def _aq_covers(window_start, window_end, probe_keys, unit_names):
    """Any aq alert (probe key match, or failed-unit for one of the unit
    names) with first_ts inside [window_start, window_end]?"""
    try:
        with _aq_ro() as a:
            for key in probe_keys:
                row = a.execute(
                    "SELECT 1 FROM alerts WHERE source='probe' AND key=? "
                    "AND first_ts BETWEEN ? AND ? LIMIT 1",
                    (key, window_start, window_end)).fetchone()
                if row:
                    return True
            for unit in unit_names:
                row = a.execute(
                    "SELECT 1 FROM alerts WHERE source='failed-unit' AND key=? "
                    "AND first_ts BETWEEN ? AND ? LIMIT 1",
                    (f"Failed unit: {unit}", window_start, window_end)).fetchone()
                if row:
                    return True
    except sqlite3.Error as e:
        print(f"aq coverage query failed (fail-open=covered): {e}")
        return True  # fail open: an unreadable aq store must never inflate the metric
    return False


def probe_undetected_downtime(lookback_hours=48, alert_grace_min=40):
    """wq#1015. Diffs each product's own forward-progress signal against aq
    alert timestamps. A gap > the product's stall threshold with no aq alert
    anywhere from onset to (onset+gap+grace) is undetected downtime."""
    c = ledger_conn()
    findings = []
    now = datetime.datetime.now(datetime.timezone.utc)

    for name, cfg in PRODUCTS.items():
        if name == "lpp":
            points = _lpp_progress_timestamps(lookback_hours)
        else:
            continue  # extension point: add a provider fn per new product
        if not points:
            continue
        stall = datetime.timedelta(minutes=cfg["stall_min"])
        window_min = (now - datetime.timedelta(hours=lookback_hours))
        # gaps between consecutive progress events...
        pairs = list(zip(points, points[1:]))
        # ...plus an ongoing stall from the last event to now.
        pairs.append((points[-1], now))
        for t0, t1 in pairs:
            if t0 < window_min:
                continue
            gap = t1 - t0
            if gap <= stall:
                continue
            gap_min = gap.total_seconds() / 60
            # aq alerts.first_ts is stored "%Y-%m-%dT%H:%M:%SZ" (see aq.py now()) --
            # match that exactly. A space-vs-'T' mismatch here silently breaks the
            # BETWEEN string comparison (space sorts before 'T', so the bound is
            # always "less than" a real timestamp) and the sensor never fires.
            w_start = t0.strftime("%Y-%m-%dT%H:%M:%SZ")
            w_end = (t0 + gap + datetime.timedelta(minutes=alert_grace_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if _aq_covers(w_start, w_end, cfg["aq_probe_keys"], cfg["aq_unit_names"]):
                continue  # SOMETHING alerted near onset -- that's wq#963 territory, not this
            desc = (f"{name}: no forward progress {t0.isoformat()} -> {t1.isoformat()} "
                    f"({gap_min:.0f}min), no aq alert in window")
            ts = t0.strftime("%Y-%m-%d")
            if ledger_insert(c, ts, "downtime", desc, f"{name}gap:{t0.isoformat()}",
                              undetected_min=int(gap_min)):
                findings.append(desc)
    c.commit()
    return len(findings), findings


def main():
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("show")
    args = ap.parse_args()
    if args.cmd == "run":
        n1, f1 = probe_unticketed_recovery()
        n2, f2 = probe_undetected_downtime()
        msg = (f"R4 sensors run: unticketed-recovery new={n1}, "
               f"undetected-downtime new={n2}")
        if f1:
            msg += "\n\nUnticketed recoveries:\n" + "\n".join(f"  {x}" for x in f1)
        if f2:
            msg += "\n\nUndetected downtime:\n" + "\n".join(f"  {x}" for x in f2)
        ntfy_digest(f"R4 sensors: {n1} unticketed, {n2} undetected-downtime", msg)
        print(json.dumps({"unticketed_new": n1, "undetected_new": n2,
                          "findings": f1 + f2}))
    elif args.cmd == "show":
        c = ledger_conn()
        rows = c.execute("SELECT * FROM events WHERE conf='sensor' "
                         "ORDER BY id DESC LIMIT 30").fetchall()
        for r in rows:
            print(r)


if __name__ == "__main__":
    main()
