#!/usr/bin/env python3
"""alert_hygiene_audit — standing quarterly alert-hygiene audit (wq#1042, R5 G4).

Applies the 90-day rule to every aq source ("acted on in 90 days? else delete or
demote") and audits required fields on page-class alerts (runbook link). READ-ONLY
on alerts.db. Writes per-source actionable-rate into the R4 intervention-ledger dir:
  - intervention_ledger.db table alert_hygiene   (additive)
  - alert_hygiene/audit_<date>.md                (human-readable)
  - digest line -> lumen-updates                 (info; never pages)

Run by lumen-alert-hygiene-audit.timer (quarterly). Complements wq#972 (one-shot)
and the R4 ledger (daily pager-load number).
"""
import os, sqlite3, datetime, subprocess, pathlib, sys

ALERTS_DB = os.environ.get("AQ_DB", "/home/lumen/agents/alert-queue/alerts.db")
LEDGER_DIR = os.environ.get("HYG_LEDGER_DIR", "/home/lumen/foreman/ops")
LEDGER_DB = os.path.join(LEDGER_DIR, "intervention_ledger.db")
REPORT_DIR = os.path.join(LEDGER_DIR, "alert_hygiene")
WINDOW_DAYS = int(os.environ.get("HYG_WINDOW_DAYS", "90"))
NTFY = os.environ.get("HYG_NTFY", "/home/lumen/bin/ntfy-send")

def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True); c.row_factory = sqlite3.Row; return c

AUTO = "(owner LIKE 'auto%' OR COALESCE(note,'') LIKE '%auto-cleared%' OR COALESCE(note,'') LIKE '%auto-ack%' OR COALESCE(note,'') LIKE '%auto-resolve%')"

def audit():
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    c = ro(ALERTS_DB)
    rows = c.execute(f"""
        SELECT source,
               COUNT(*) rows, COALESCE(SUM(count),0) vol,
               SUM(CASE WHEN status IN ('acked','resolved') AND NOT {AUTO} THEN 1 ELSE 0 END) actioned,
               SUM(CASE WHEN {AUTO} THEN 1 ELSE 0 END) auto_handled,
               SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) ignored_open
        FROM alerts WHERE last_ts > ? GROUP BY source ORDER BY vol DESC""", (cutoff,)).fetchall()

    # page-class runbook coverage (heuristic): source on the page topic, or high/urgent.
    # covered = a runbook mention AND a link (avoids 'no runbook here' false positives)
    runbook_like = "((COALESCE(message,'') LIKE '%runbook%' OR COALESCE(note,'') LIKE '%runbook%') AND (COALESCE(message,'') LIKE '%http%' OR COALESCE(note,'') LIKE '%http%' OR COALESCE(message,'') LIKE '%/runbooks/%'))"
    page_rows = c.execute(f"""
        SELECT COUNT(*) tot, SUM(CASE WHEN {runbook_like} THEN 1 ELSE 0 END) with_rb
        FROM alerts WHERE last_ts > ? AND (source='ntfy:lumen-alerts'
              OR priority IN ('4','5','high','urgent'))""", (cutoff,)).fetchone()
    c.close()

    results = []
    for r in rows:
        rate = (r["actioned"] / r["rows"]) if r["rows"] else 0.0
        # The 90-day rule targets alerts that pile up OPEN with no human action —
        # NOT self-clearing sources (probe/storage edge-clear: high auto_handled,
        # low ignored_open) which are working as designed.
        if r["ignored_open"] >= 20 and rate < 0.05:
            verdict = "DELETE/DEMOTE candidate (%d open, ~0 acted on in %dd)" % (r["ignored_open"], WINDOW_DAYS)
        elif r["ignored_open"] >= 10 and rate < 0.10:
            verdict = "REVIEW (%d open, low action rate)" % r["ignored_open"]
        else:
            verdict = "keep"
        results.append((r["source"], r["rows"], r["vol"], r["actioned"],
                        r["auto_handled"], r["ignored_open"], round(rate, 3), verdict))
    return results, page_rows

def persist(results):
    os.makedirs(REPORT_DIR, exist_ok=True)
    c = sqlite3.connect(LEDGER_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS alert_hygiene(
        run_ts TEXT, source TEXT, window_days INTEGER, rows INTEGER, volume INTEGER,
        actioned INTEGER, auto_handled INTEGER, ignored_open INTEGER,
        actionable_rate REAL, verdict TEXT)""")
    ts = now()
    for (src, rw, vol, act, auto, ign, rate, verdict) in results:
        c.execute("INSERT INTO alert_hygiene VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (ts, src, WINDOW_DAYS, rw, vol, act, auto, ign, rate, verdict))
    c.commit(); c.close()
    return ts

def write_report(ts, results, page_rows):
    day = ts[:10]
    path = os.path.join(REPORT_DIR, f"audit_{day}.md")
    cand = [r for r in results if r[7] != "keep"]
    rb_tot = page_rows["tot"] or 0; rb_with = page_rows["with_rb"] or 0
    rb_cov = (rb_with / rb_tot * 100) if rb_tot else 100.0
    with open(path, "w") as f:
        f.write(f"# Alert-hygiene audit {day} (window {WINDOW_DAYS}d)\n\n")
        f.write(f"Sources: {len(results)}. Delete/demote/review candidates: {len(cand)}.\n")
        f.write(f"Page-class runbook coverage: {rb_with}/{rb_tot} = {rb_cov:.0f}%%\n\n".replace("%%","%"))
        f.write("## Per-source (by volume)\n")
        f.write("source | rows | vol | actioned | auto | open | rate | verdict\n")
        f.write("---|---|---|---|---|---|---|---\n")
        for (src, rw, vol, act, auto, ign, rate, verdict) in results:
            f.write(f"{src} | {rw} | {vol} | {act} | {auto} | {ign} | {rate} | {verdict}\n")
        if cand:
            f.write("\n## Action needed (90-day rule)\n")
            for (src, rw, vol, act, *_rest, verdict) in [(r[0],r[1],r[2],r[3],r[6],r[7]) for r in cand]:
                f.write(f"- **{src}**: {verdict} — {rw} alerts, {act} acted on. Delete the source, demote to digest, or justify.\n")
        if rb_cov < 100:
            f.write(f"\n## Page-class required fields\n- {rb_tot-rb_with}/{rb_tot} page-class alerts carry NO runbook reference. "
                    f"Every page-level alert should carry a runbook link (R5 §2). aq_escalate seeds one in its P0 tickets; "
                    f"rank-and-file probe/page sources should too.\n")
    return path, len(cand), rb_cov

def main():
    results, page_rows = audit()
    ts = persist(results)
    path, ncand, rb_cov = write_report(ts, results, page_rows)
    msg = (f"Alert-hygiene audit {ts[:10]}: {len(results)} sources, {ncand} delete/demote/review "
           f"candidate(s), page-class runbook coverage {rb_cov:.0f}%. Report: {path}")
    print(msg)
    if os.path.exists(NTFY):
        try:
            subprocess.run([NTFY, "-p", "low", "-t", "lumen-updates",
                            "-T", "alert-hygiene audit (quarterly)", msg], timeout=15)
        except Exception as e:
            print(f"digest publish skipped: {e}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
