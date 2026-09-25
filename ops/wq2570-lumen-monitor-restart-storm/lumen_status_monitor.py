#!/usr/bin/env python3
"""
Lumen Status Monitor — runs every 60s, writes JSON snapshot
Claude reads this instead of polling services individually.
Output: /home/lumen/scratch/STATUS.json
"""
import json, logging, requests, sqlite3, subprocess, time, psutil, traceback
from pathlib import Path
from datetime import datetime, timezone

OUT = Path("/home/lumen/scratch/STATUS.json")
AGENT_DIR = Path("/home/lumen/agent")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lumen-monitor")

def svc(name):
    try:
        r = subprocess.run(["systemctl","is-active",name],capture_output=True,text=True)
        return r.stdout.strip()
    except: return "unknown"

def messenger(path):
    try:
        r = requests.get(f"http://localhost:8765{path}",timeout=3)
        return r.json()
    except: return {}

def research_stats():
    rd = Path("/home/lumen/scratch/research")
    if not rd.exists(): return {"count":0}
    files = list(rd.glob("*.md"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_files = [f for f in files if f.name.startswith(today)]
    return {"count":len(files),"today":len(today_files),"latest":today_files[-1].name if today_files else ""}

def project_stats():
    db = AGENT_DIR / "project_engine.db"
    if not db.exists(): return {}
    conn = sqlite3.connect(str(db))
    complete = conn.execute('SELECT COUNT(*) FROM projects WHERE status="complete"').fetchone()[0]
    active = conn.execute('SELECT name FROM projects WHERE status="active"').fetchall()
    conn.close()
    return {"complete":complete,"active":[r[0] for r in active]}

# wq#1024: STATUS.json has two independent writers (this monitor + lumen-agent's
# write_status()) on incompatible schemas. Whole-file overwrite meant "last writer
# wins" flipped the file's shape every ~60s once lumen-agent runs, and consumers
# keyed to the OTHER writer's schema (e.g. dashboard_api.py's status.get("agent",
# "unknown")) would silently read the wrong default half the time. Fix: each writer
# now merges its own top-level keys into whatever is already on disk instead of
# replacing the file, so the two schemas coexist as a union rather than racing.
# This writer owns: ts, services, ram, orchestrator, research, projects.
# A merge fixes the schema clash but, on its own, means a writer that crashes or
# stops leaves its last keys on disk forever with no way to tell they've gone
# stale (the file's own mtime updates on every OTHER writer's cycle too, so it
# can't be used as a per-writer freshness signal). "_writers" is a small
# deep-merged namespace — not clobbered by the top-level update() below — each
# writer stamps its own sub-key on every write so a consumer that cares CAN check
# "is the writer that owns this key still alive" without every reader needing to.
WRITER_ID = "lumen-monitor"

def merged_write(own_keys: dict):
    try:
        existing = json.loads(OUT.read_text())
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}
    writers = existing.get("_writers", {})
    if not isinstance(writers, dict):
        writers = {}
    writers[WRITER_ID] = datetime.now(timezone.utc).isoformat()
    existing.update(own_keys)
    existing["_writers"] = writers
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2))
    tmp.replace(OUT)

# wq#2570: this loop had no exception handling at all. svc()/messenger() catch
# their own errors, but project_stats() (sqlite3 against a db shared with other
# writers, per the wq#1024 comment above) and merged_write()'s file I/O did not —
# any transient fault there (e.g. "database is locked") was fatal to the whole
# process. Restart=always with a flat 5s RestartSec and no StartLimitBurst then
# respawned it forever with no backoff and no alert, which is how this unit hit
# NRestarts=3197 silently between 2026-08-31 and 2026-09-16. Catching everything
# here means a transient fault gets logged and retried on the next 60s tick
# in-process instead of killing the process; the systemd unit (see
# lumen-monitor.service in this directory) is the backstop for a fault that
# isn't transient.
while True:
    try:
        mem = psutil.virtual_memory()
        orch = messenger("/orchestrator")
        own = {
            "ts": datetime.now(timezone.utc).isoformat(),
            # wq#974: dropped lumen-builder (unit exists but disabled, never run — no
            # project_engine.log ever written; genuinely orphaned, tracked separately
            # for a real retire decision). lumen-orchestrator kept: verified ACTIVE,
            # not retired (ticket's premise was wrong for this one).
            "services": {s:svc(s) for s in ["lumen-agent","lumen-messenger","lumen-orchestrator"]},
            "ram": {"used_mb":mem.used//1024//1024,"total_mb":mem.total//1024//1024,"avail_mb":mem.available//1024//1024,"pct":round(mem.percent,1)},
            "orchestrator": {"processed":orch.get("processed"),"total":orch.get("total"),"hv":orch.get("high_value_count"),"running":orch.get("running")},
            "research": research_stats(),
            "projects": project_stats(),
        }
        merged_write(own)
    except Exception:
        log.error("monitor cycle failed, retrying next tick:\n%s", traceback.format_exc())
    time.sleep(60)
