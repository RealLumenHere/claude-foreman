#!/usr/bin/env python3
"""R4 intervention ledger — measures whether the fleet needs Frank more or less.
Metrics (per ISO week):
  a  frank_fix     Frank had to initiate a fix
  b  frank_detect  Frank discovered a failure the fleet should have caught
  e  frank_repeat  Frank had to repeat himself
  c  parked        tickets parked on Frank (daily snapshot; weekly = mean+last)
  d  undetected_min  fleet downtime minutes not self-detected
Headline = distinct intervention EVENTS (a|b|e) per week, trending.
Sources (ALL read-only): minerva.db archival_notes, queue.db, alerts.db,
Claude session transcripts (~/.claude/projects — Frank's chat-origin signal,
mined automatically since wq#1014 so the ledger no longer depends on anyone
remembering to annotate), plus manual events (add-event) for incidents
documented only in prose.
Own store: intervention_ledger.db (this dir). Outputs: glance assets JSON +
telemetry JSON. Structured markers (higher precision than regex, put them in
episode commits / ticket notes going forward):
  FRANK-FIX: <desc>   FRANK-DETECT: <desc>   FRANK-REPEAT: <desc>
  DOWNTIME-UNDETECTED-MIN=<n>
"""
import argparse, datetime, json, os, re, sqlite3, sys, tempfile

D = os.path.dirname(os.path.abspath(__file__))
LEDGER_DB = os.path.join(D, "intervention_ledger.db")
MINERVA = "/home/lumen/minerva/minerva.db"
QUEUE = "/home/lumen/agents/work-queue/queue.db"
GLANCE_OUT = "/home/lumen/glance/assets/ledger.json"
TELEM_OUT = "/home/lumen/telemetry/intervention_ledger.json"

# Mirrors mission-control app.py REVIEW_HUMAN_RE so parked counts reconcile.
HUMAN_RE = re.compile(r"\b(frank|gate|approval)\b", re.I)

MARK_FIX = re.compile(r"FRANK-FIX:\s*(.{0,160})")
MARK_DET = re.compile(r"FRANK-DETECT:\s*(.{0,160})")
MARK_REP = re.compile(r"FRANK-REPEAT:\s*(.{0,160})")
MARK_DT = re.compile(r"DOWNTIME-UNDETECTED-MIN=(\d+)")

# Fallback regex classifiers (lower confidence, conf=regex). Deliberately
# conservative: false positives inflate the baseline we are trying to shrink.
RX_FIX = re.compile(r"Frank (reported|flagged|had to (fix|intervene|step in)|"
                    r"direct(ed|ive)|asked (to|for a) fix|commissioned)", re.I)
RX_DET = re.compile(r"Frank (noticed|discovered|caught|found)|Frank was right",
                    re.I)
RX_REP = re.compile(r"Frank ha[sd] (corrected|said|stated).{0,40}"
                    r"(again|repeatedly|before)|had to say it again|"
                    r"repeat(ed)? himself", re.I)

# --- Chat-origin mining (wq#1014). Every Frank intervention arrives as a
# type:"user" turn in a session transcript; that log is written by the harness,
# not by anyone's memory, so coverage is structural. Design:
# ops/R4_frank_marker_autoemit_design.md
CHAT_ROOT = "/home/lumen/.claude/projects"
# Headless worker/cron sessions also log "user" turns (harness prompts, tool
# results) — a session showing any sentinel is discarded wholesale.
HARNESS_SENTINELS = ("You are a Lumen fleet", "YOUR TICKET: wq#",
                     "<<autonomous-loop")
WORKTREE_PROJ_PREFIX = "-home-lumen-worktrees-"
INJECTED_PREFIXES = ("<command-name>", "<local-command-stdout>",
                     "<system-reminder>", "Caveat:", "[Request interrupted")
# Frank-voice classifiers; precedence detect > repeat > fix mirrors the
# report's b > e > a damning-ness rule. Conservative: no match = no event.
CHAT_DET = re.compile(
    r"\b(?:is|are|went|looks?|seems?)\s+(?:completely\s+|totally\s+)?"
    r"(?:down|dead|broken|offline|stuck|hung|frozen)\b"
    r"|\bnot\s+work(?:ing)?\b|\bstopped\s+working\b|\bcrashed\b"
    r"|\bwhy\s+is\b.{0,50}\b(?:failing|down|broken|empty|stale|dead)\b"
    r"|\bsomething(?:'s| is)\s+(?:wrong|off|broken)\b", re.I)
CHAT_REP = re.compile(
    r"\bi(?:'ve| have)?\s+(?:already\s+|just\s+)?(?:told|said|asked)\b.{0,60}"
    r"\b(?:again|before|already)\b"
    r"|\bhow\s+many\s+times\b|\b(?:second|third)\s+time\b"
    r"|\bstill\s+(?:broken|down|dead|wrong|not\s+working)\b", re.I)
CHAT_FIX = re.compile(
    r"\b(?:please\s+)?fix\s+(?:it|this|that|the)\b|\bneeds?\s+fixing\b"
    r"|\brestart\s+(?:it|the)\b|\brevert\b|\broll\s?back\b"
    r"|\bget\s+(?:it|this|that)\s+(?:working|back\s+up)\b"
    r"|\bundo\s+(?:that|this)\b", re.I)
# Instinct 3: token-shaped substrings never reach the DB.
TOKEN_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}"
                      r"|eyJ[A-Za-z0-9_.-]{10,}|[A-Za-z0-9+/=]{40,})\b")


def db():
    c = sqlite3.connect(LEDGER_DB, timeout=15)
    c.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
        desc TEXT, source_ref TEXT, conf TEXT DEFAULT 'regex',
        undetected_min INTEGER DEFAULT 0,
        UNIQUE(ts, kind, source_ref))""")
    c.execute("""CREATE TABLE IF NOT EXISTS parked_snapshots(
        ts TEXT PRIMARY KEY, blocked_human INTEGER, user_open INTEGER,
        total INTEGER)""")
    return c


def ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def week_of(ts):
    d = datetime.date.fromisoformat(ts[:10])
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def classify(text):
    """Return list of (kind, desc, conf, undetected_min)."""
    out = []
    for rx, kind in ((MARK_FIX, "fix"), (MARK_DET, "detect"),
                     (MARK_REP, "repeat")):
        for m in rx.finditer(text):
            out.append((kind, m.group(1).strip(), "marker", 0))
    for m in MARK_DT.finditer(text):
        out.append(("downtime", "undetected downtime", "marker",
                    int(m.group(1))))
    if not out:  # fall back to regex only when no explicit markers
        if RX_DET.search(text):
            out.append(("detect", text[:160], "regex", 0))
        elif RX_FIX.search(text):
            out.append(("fix", text[:160], "regex", 0))
        if RX_REP.search(text):
            out.append(("repeat", text[:160], "regex", 0))
    return out


def classify_chat(text):
    """Classify one Frank-typed message; returns kind or None."""
    for rx, kind in ((CHAT_DET, "detect"), (CHAT_REP, "repeat"),
                     (CHAT_FIX, "fix")):
        if rx.search(text):
            return kind
    return None


def _chat_texts(path):
    """(date, text) list of Frank-typed turns in one transcript, or None if
    the file is a headless harness session."""
    out = []
    with open(path, errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("type") != "user" or o.get("isSidechain"):
                continue
            if o.get("toolUseResult") is not None:
                continue
            content = (o.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                if any(isinstance(b, dict) and b.get("type") == "tool_result"
                       for b in content):
                    continue
                text = "\n".join(b.get("text", "") for b in content
                                 if isinstance(b, dict)
                                 and b.get("type") == "text")
            else:
                continue
            text = (text or "").strip()
            if not text:
                continue
            if any(s in text for s in HARNESS_SENTINELS):
                return None
            if text.startswith(INJECTED_PREFIXES):
                continue
            ts = (o.get("timestamp") or "")[:10]
            if len(ts) == 10:
                out.append((ts, text))
    return out


def chat_events(since, root=CHAT_ROOT):
    """Yield (ts, kind, desc, source_ref) mined from session transcripts.
    One event per (session, kind, day) — a burst about one outage is one
    intervention, not five."""
    if not os.path.isdir(root):
        return
    try:
        cutoff = (datetime.datetime.fromisoformat(since[:10]).timestamp()
                  - 86400)
    except ValueError:
        cutoff = None
    for proj in sorted(os.listdir(root)):
        pdir = os.path.join(root, proj)
        if proj.startswith(WORKTREE_PROJ_PREFIX) or not os.path.isdir(pdir):
            continue
        for fn in sorted(os.listdir(pdir)):
            if not fn.endswith(".jsonl"):
                continue
            path = os.path.join(pdir, fn)
            try:
                if cutoff and os.path.getmtime(path) < cutoff:
                    continue
                msgs = _chat_texts(path)
            except OSError:
                continue
            firsts = {}
            for date, text in (msgs or []):
                if date < since[:10]:
                    continue
                kind = classify_chat(text)
                if kind:
                    firsts.setdefault((kind, date), text)
            sid = fn[:-6][:8]
            for (kind, date), text in sorted(firsts.items()):
                desc = TOKEN_RE.sub("[MASKED]", text)[:160]
                yield (date, kind, desc, f"chat:{proj}:{sid}:{date}")


def collect_chat(c, since, root=CHAT_ROOT):
    """Insert conf='chat' events; idempotent via UNIQUE(ts,kind,source_ref)."""
    n = 0
    for ts, kind, desc, ref in chat_events(since, root):
        before = c.total_changes
        c.execute("INSERT OR IGNORE INTO events"
                  "(ts,kind,desc,source_ref,conf,undetected_min) "
                  "VALUES(?,?,?,?,'chat',0)", (ts, kind, desc, ref))
        n += c.total_changes - before
    c.commit()
    return n


def collect(since):
    c = db()
    n = 0
    # Episodes
    with ro(MINERVA) as m:
        rows = m.execute(
            "SELECT id, created_at, title, content FROM archival_notes "
            "WHERE created_at >= ? AND kind IS NOT NULL", (since,)).fetchall()
    for r in rows:
        text = (r["title"] or "") + "\n" + (r["content"] or "")
        for kind, desc, conf, dt in classify(text):
            try:
                c.execute("INSERT OR IGNORE INTO events"
                          "(ts,kind,desc,source_ref,conf,undetected_min) "
                          "VALUES(?,?,?,?,?,?)",
                          (r["created_at"][:10], kind, desc,
                           f"episode:{r['id']}", conf, dt))
                n += c.total_changes and 1
            except sqlite3.Error:
                pass
    # Ticket notes
    with ro(QUEUE) as q:
        rows = q.execute(
            "SELECT id, created_at, notes, frank_response, response_ts "
            "FROM tasks WHERE created_at >= ? OR response_ts >= ?",
            (since, since)).fetchall()
    for r in rows:
        text = (r["notes"] or "")
        for kind, desc, conf, dt in classify(text):
            c.execute("INSERT OR IGNORE INTO events"
                      "(ts,kind,desc,source_ref,conf,undetected_min) "
                      "VALUES(?,?,?,?,?,?)",
                      ((r["created_at"] or since)[:10], kind, desc,
                       f"wq:{r['id']}", conf, dt))
        # Every frank_response on a gated ticket = Frank answered something.
        if r["frank_response"] and (r["response_ts"] or "") >= since:
            c.execute("INSERT OR IGNORE INTO events"
                      "(ts,kind,desc,source_ref,conf,undetected_min) "
                      "VALUES(?,?,?,?,?,?)",
                      (r["response_ts"][:10], "fix",
                       f"gated decision answered: {(r['frank_response'] or '')[:120]}",
                       f"wq:{r['id']}:response", "structured", 0))
    c.commit()
    return c


def snapshot_parked(c):
    with ro(QUEUE) as q:
        blocked = q.execute(
            "SELECT id, coalesce(block_reason,'')||' '||coalesce(notes,'')"
            "||' '||coalesce(gate,'') t FROM tasks WHERE status='blocked'"
        ).fetchall()
        blocked_human = sum(1 for r in blocked if HUMAN_RE.search(r["t"]))
        user_open = q.execute(
            "SELECT COUNT(*) n FROM tasks WHERE lane='user' AND "
            "status IN ('open','blocked')").fetchone()["n"]
    today = datetime.date.today().isoformat()
    c.execute("INSERT OR REPLACE INTO parked_snapshots VALUES(?,?,?,?)",
              (today, blocked_human, user_open, blocked_human + user_open))
    c.commit()
    return blocked_human + user_open


def rollup(c):
    weeks = {}
    rows = c.execute(
        "SELECT ts, kind, conf, undetected_min FROM events").fetchall()
    # wq#1014: a regex event is the low-confidence sighting of an incident the
    # chat miner saw directly — same (kind, day) counts once, chat wins. Only
    # chat suppresses regex, so pre-chat history (W34=13 baseline) is unchanged.
    chat_days = {(r[0], r[1]) for r in rows if r[2] == "chat"}
    for r in rows:
        if r[2] == "regex" and (r[0], r[1]) in chat_days:
            continue
        w = week_of(r[0])
        d = weeks.setdefault(w, {"fix": 0, "detect": 0, "repeat": 0,
                                 "events": 0, "undetected_min": 0,
                                 "regex_share": 0, "_n": 0})
        if r[1] in ("fix", "detect", "repeat"):
            d[r[1]] += 1
            d["events"] += 1
            d["_n"] += 1
            if r[2] == "regex":
                d["regex_share"] += 1
        d["undetected_min"] += r[3] or 0
    for w, d in weeks.items():
        d["regex_share"] = round(d["regex_share"] / d["_n"], 2) if d["_n"] else 0
        d.pop("_n")
        pk = c.execute(
            "SELECT AVG(total), MAX(ts) FROM parked_snapshots "
            "WHERE ts >= date(?, 'weekday 0','-6 days') AND ts <= ?",
            (_week_end(w), _week_end(w))).fetchone()
        last = c.execute("SELECT total FROM parked_snapshots WHERE ts=("
                         "SELECT MAX(ts) FROM parked_snapshots WHERE ts<=?)",
                         (_week_end(w),)).fetchone()
        d["parked_avg"] = round(pk[0], 1) if pk and pk[0] else None
        d["parked_last"] = last[0] if last else None
    return dict(sorted(weeks.items()))


def _week_end(w):
    y, wk = w.split("-W")
    return datetime.date.fromisocalendar(int(y), int(wk), 7).isoformat()


def _week_start(w):
    y, wk = w.split("-W")
    return datetime.date.fromisocalendar(int(y), int(wk), 1)


def events_through_weekday(c, w, dow):
    """Count intervention events (fix|detect|repeat) in ISO week `w` up to and
    including its `dow`-th day (1=Mon..7=Sun). Used for a fair same-point pace
    comparison of a partial current week against the prior week."""
    cutoff = (_week_start(w) + datetime.timedelta(days=dow - 1)).isoformat()
    start = _week_start(w).isoformat()
    row = c.execute(
        "SELECT COUNT(*) FROM events WHERE kind IN('fix','detect','repeat') "
        "AND ts >= ? AND ts <= ?", (start, cutoff)).fetchone()
    return row[0] if row else 0


def emit(c):
    weeks = rollup(c)
    # Always show the CURRENT week-to-date, even at 0 events — the question
    # is "this week vs last week", not "last week that had incidents".
    cw = week_of(datetime.date.today().isoformat())
    if cw not in weeks:
        weeks[cw] = {"fix": 0, "detect": 0, "repeat": 0, "events": 0,
                     "undetected_min": 0, "regex_share": 0}
        last = c.execute("SELECT total FROM parked_snapshots "
                         "WHERE ts=(SELECT MAX(ts) FROM parked_snapshots)"
                         ).fetchone()
        weeks[cw]["parked_avg"] = None
        weeks[cw]["parked_last"] = last[0] if last else None
        weeks = dict(sorted(weeks.items()))
    keys = list(weeks.keys())
    today = datetime.date.today()
    cw = week_of(today.isoformat())
    # HEADLINE/TREND compare the last COMPLETE week vs the week before it — never
    # a partial current week (that arrow lies Mon-Wed; see R4_partialweek_fix_design.md).
    complete = [k for k in keys if k != cw]
    head_wk = complete[-1] if complete else None
    prev_wk = complete[-2] if len(complete) > 1 else None
    headd = weeks.get(head_wk, {})
    prevd = weeks.get(prev_wk, {})
    delta = (headd.get("events", 0) - prevd.get("events", 0)) if prev_wk else None
    trend = "flat"
    if delta is not None:
        # down (fewer interventions) = good/green; up = bad/red.
        trend = "down" if delta < 0 else ("up" if delta > 0 else "flat")
    # CURRENT partial week rides along, never drives the color. Pace = events so
    # far this week minus the prior week's events through the SAME weekday.
    in_prog = None
    if cw in weeks:
        curd = weeks[cw]
        dow = today.isoweekday()
        prior_pace = events_through_weekday(c, head_wk, dow) if head_wk else 0
        cur_events = curd.get("events", 0)
        in_prog = {
            "week": cw, "dow": dow,
            "events_to_date": cur_events,
            "pace_vs_prev": (cur_events - prior_pace),
            "prev_through_same_day": prior_pace,
            "compared_to": head_wk,
            "parked_last": curd.get("parked_last"),
            "undetected_min": curd.get("undetected_min", 0),
        }
    out = {
        "generated": datetime.datetime.now(datetime.timezone.utc
                                           ).strftime("%Y-%m-%dT%H:%MZ"),
        "headline": headd.get("events", 0),
        "week": head_wk, "trend": trend, "delta": delta,
        "current": {"week": head_wk, **headd} if head_wk else {},
        "previous": {"week": prev_wk, **prevd} if prev_wk else None,
        "in_progress": in_prog,
        "weeks": [{"week": k, **v} for k, v in weeks.items()][-8:],
        "note": "headline = Frank intervention events in the last COMPLETE week "
                "vs the week before (down=fleet needs Frank less). in_progress = "
                "current week to date, paced against the same weekday last week.",
    }
    for path in (GLANCE_OUT, TELEM_OUT):
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
            with os.fdopen(fd, "w") as f:
                json.dump(out, f, indent=1)
            os.replace(tmp, path)
            os.chmod(path, 0o644)  # glance container reads as non-owner
        except OSError as e:
            print(f"WARN emit {path}: {e}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="daily: collect+snapshot+emit")
    r.add_argument("--since", default=None,
                   help="collect episodes/tickets since (default 14d back)")
    b = sub.add_parser("backfill")
    b.add_argument("--since", required=True)
    a = sub.add_parser("add-event", help="manual documented incident")
    a.add_argument("--ts", required=True)
    a.add_argument("--kind", required=True,
                   choices=["fix", "detect", "repeat", "downtime"])
    a.add_argument("--desc", required=True)
    a.add_argument("--ref", required=True)
    a.add_argument("--undetected-min", type=int, default=0)
    m = sub.add_parser("mine-chat",
                       help="mine Frank chat transcripts (wq#1014)")
    m.add_argument("--since", default=None)
    m.add_argument("--root", default=CHAT_ROOT)
    m.add_argument("--dry-run", action="store_true",
                   help="print would-be events, write nothing")
    sub.add_parser("show")
    args = ap.parse_args()

    if args.cmd == "add-event":
        c = db()
        c.execute("INSERT OR IGNORE INTO events"
                  "(ts,kind,desc,source_ref,conf,undetected_min) "
                  "VALUES(?,?,?,?, 'manual', ?)",
                  (args.ts, args.kind, args.desc, args.ref,
                   args.undetected_min))
        c.commit()
        emit(c)
        print("added + emitted")
        return
    if args.cmd == "mine-chat":
        since = args.since or (datetime.date.today()
                               - datetime.timedelta(days=14)).isoformat()
        if args.dry_run:
            for ev in chat_events(since, args.root):
                print(json.dumps(dict(zip(("ts", "kind", "desc", "ref"), ev))))
            return
        c = db()
        print(json.dumps({"inserted": collect_chat(c, since, args.root)}))
        return
    if args.cmd in ("run", "backfill"):
        since = args.since or (datetime.date.today()
                               - datetime.timedelta(days=14)).isoformat()
        c = collect(since)
        # Chat mining must never take down the daily run: degrade to a WARN.
        try:
            collect_chat(c, since)
        except Exception as e:
            print(f"WARN chat mining failed: {e}", file=sys.stderr)
        if args.cmd == "run":
            snapshot_parked(c)
        out = emit(c)
        print(json.dumps({"headline": out["headline"], "week": out["week"],
                          "trend": out["trend"]}))
        return
    if args.cmd == "show":
        c = db()
        print(json.dumps(rollup(c), indent=1))


if __name__ == "__main__":
    main()
