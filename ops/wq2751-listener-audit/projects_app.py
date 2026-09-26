#!/usr/bin/env python3
"""
projects.lumennode v2 — unified project + agent health dashboard.

Reads /home/lumen/projects.json + auto-discovers agent health from:
  - systemd timers (lumen-*, minerva-*, radar-*)
  - cron entries (~/research/* logs)
  - container status (docker ps)
  - Log file mtimes (alert if "should run daily" hasn't logged in 30h)

Single page, mobile-first, zero deps (stdlib only).
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

PROJECTS_FILE = Path("/home/lumen/projects.json")
HANDOFF_DIR = Path("/home/lumen/projects/_handoffs")
HANDOFF_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")

# Logs we monitor for "should have run recently"
HEALTH_CHECKS = [
    # (name, log path, max age in hours, what it does)
    ("radar-watcher",      "/home/lumen/logs/radar_watcher.log",      30, "Nightly GitHub repo + release watcher"),
    ("radar-dispatcher",   "/home/lumen/logs/radar_dispatcher.log",   180, "Weekly research dispatcher (Sundays 2am)"),
    ("kb-dispatcher",      "/home/lumen/logs/kb_dispatcher.log",      180, "Weekly knowledge base ingestion"),
    ("lumen-watchdog",     "/home/lumen/logs/watchdog.log",            1, "5-minute health check"),
    ("fleet-status",       None,                                       None, "Fleet HTTP status endpoint"),
    ("projects-app",       None,                                       None, "This dashboard"),
    ("cloudflared",        None,                                       None, "Public-facing tunnel (lumennode.uk)"),
    ("article-ingest",     "/home/lumen/logs/article_ingest.log",     2, "Article queue drain (every 15min)"),
]


def run(cmd, timeout=5):
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL,
                                       timeout=timeout, text=True).strip()
    except Exception:
        return ""


def systemd_active(unit):
    return run(f"systemctl is-active {unit}") == "active"


def file_age_hours(p):
    try:
        return (time.time() - Path(p).stat().st_mtime) / 3600
    except Exception:
        return None


def health_snapshot():
    out = {"checks": [], "timers": [], "containers": [], "fleet": {}}

    # Health checks (log file mtime + known systemd services)
    for name, log, max_h, desc in HEALTH_CHECKS:
        check = {"name": name, "desc": desc, "status": "unknown",
                 "detail": "", "age_h": None}
        if log:
            age = file_age_hours(log)
            check["age_h"] = round(age, 1) if age is not None else None
            if age is None:
                check["status"] = "missing"
                check["detail"] = f"log not found"
            elif age > max_h * 2:
                check["status"] = "critical"
                check["detail"] = f"last run {pretty_age(age)} ago (limit {max_h}h)"
            elif age > max_h:
                check["status"] = "warn"
                check["detail"] = f"last run {pretty_age(age)} ago"
            else:
                check["status"] = "ok"
                check["detail"] = f"last {pretty_age(age)} ago"
        else:
            # Service check
            active = systemd_active(name)
            check["status"] = "ok" if active else "critical"
            check["detail"] = "active" if active else "inactive"
        out["checks"].append(check)

    # Recent systemd timer activity (last 24h)
    try:
        raw = run("systemctl list-timers --all --no-pager 2>/dev/null", timeout=8)
        for line in raw.splitlines()[1:]:
            parts = line.split()
            if "LAST" in line or "ACTIVATES" in line or not parts:
                continue
            if "lumen" in line.lower() or "minerva" in line.lower() or "radar" in line.lower():
                # Find unit and last-run
                if ".timer" in line:
                    unit_match = re.search(r'(\S+\.timer)', line)
                    if unit_match:
                        unit = unit_match.group(1)
                        out["timers"].append({"unit": unit, "raw": line[:120]})
    except Exception:
        pass
    out["timers"] = out["timers"][:15]

    # Container check (key ones only)
    key_containers = ["litellm", "librechat", "vaultwarden", "ntfy",
                      "langfuse-web", "glance"]
    try:
        raw = run("docker ps --format '{{.Names}}|{{.Status}}'", timeout=5)
        running = {}
        for line in raw.splitlines():
            if "|" in line:
                n, s = line.split("|", 1)
                running[n] = s
        for k in key_containers:
            found = next((v for n, v in running.items() if k in n.lower()), None)
            out["containers"].append({
                "name": k,
                "status": "ok" if found else "missing",
                "detail": found or "not running"
            })
    except Exception:
        pass

    # Fleet status (from local fleet-status endpoint)
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:7070/status", timeout=2) as r:
            d = json.loads(r.read().decode())
            out["fleet"] = {
                "hostname": d.get("hostname"),
                "cpu_pct": d.get("cpu_pct"),
                "ram_pct": d.get("ram_pct"),
                "disk_pct": d.get("disk_pct"),
                "uptime": d.get("uptime_str"),
                "load": d.get("load"),
                "containers": len(d.get("containers", [])),
                "services_active": sum(1 for v in d.get("services", {}).values() if v == "active"),
                "services_total": len(d.get("services", {})),
            }
    except Exception:
        pass

    # Article queue metrics
    try:
        qdir = Path("/home/lumen/research/article_queue")
        out["article_queue"] = {
            "inbox": len(list((qdir / "inbox").iterdir())) if (qdir / "inbox").exists() else 0,
            "done":  len(list((qdir / "done").iterdir()))  if (qdir / "done").exists()  else 0,
            "failed": len(list((qdir / "failed").iterdir())) if (qdir / "failed").exists() else 0,
        }
    except Exception:
        out["article_queue"] = {"inbox": 0, "done": 0, "failed": 0}

    out["updated_at"] = datetime.now(PT).isoformat()
    return out


def pretty_age(hours):
    if hours < 1:
        return f"{int(hours*60)}m"
    if hours < 24:
        return f"{int(hours)}h"
    return f"{int(hours/24)}d"


PAGE = """<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#0a0a0a">
<title>Frank / Status</title>
<style>
:root {
  --bg:#0a0a0a;--card:#141414;--fg:#e8e8e8;--dim:#7a7a7a;--muted:#4a4a4a;
  --accent:#00ff88;--warn:#ffaa00;--err:#ff3355;--ok:#00ff88;--line:#1f1f1f;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--fg);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
body{padding:0 0 80px;min-height:100vh}
header{padding:16px 18px 12px;border-bottom:1px solid var(--line);position:sticky;top:0;background:rgba(10,10,10,.95);backdrop-filter:blur(6px);z-index:10}
.brand{font-size:11px;color:var(--accent);letter-spacing:2px;font-weight:600}
.brand::before{content:"\u258C "}
h1{font-size:18px;font-weight:600;margin-top:4px}
.tabs{display:flex;gap:0;margin-top:14px;border-bottom:1px solid var(--line);margin-left:-18px;margin-right:-18px;padding:0 18px}
.tab{padding:8px 16px;color:var(--dim);font-size:12px;cursor:pointer;letter-spacing:1px;border-bottom:2px solid transparent;user-select:none;text-transform:uppercase}
.tab.on{color:var(--accent);border-bottom-color:var(--accent)}
.tab .badge{display:inline-block;background:var(--err);color:#000;font-size:9px;padding:1px 5px;border-radius:8px;margin-left:6px;font-weight:bold;letter-spacing:0}
.tab.on .badge{background:var(--err)}
.filters{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
.filter{padding:3px 9px;background:var(--card);border:1px solid var(--line);color:var(--dim);font-size:10px;cursor:pointer;border-radius:2px;letter-spacing:.5px;user-select:none;text-transform:uppercase}
.filter.on{color:var(--accent);border-color:var(--accent)}
main{padding:12px 12px}
.section{display:none}
.section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--muted);padding:13px 13px 11px;margin-bottom:9px;border-radius:3px;position:relative}
.card.h-green,.card.h-ok{border-left-color:var(--ok)}
.card.h-yellow,.card.h-warn{border-left-color:var(--warn)}
.card.h-red,.card.h-critical,.card.h-missing{border-left-color:var(--err)}
.card .name{font-size:14px;font-weight:600;margin-bottom:5px;padding-right:60px}
.card .name .brand-tag{font-size:9px;padding:2px 6px;background:var(--bg);color:var(--dim);margin-left:8px;letter-spacing:1px;border-radius:2px;vertical-align:middle;font-weight:400}
.card .name .brand-tinywhale{color:var(--accent);border:1px solid var(--accent)}
.card .summary{font-size:12px;color:var(--dim);margin-bottom:8px;line-height:1.5}
.card .row{display:flex;justify-content:space-between;font-size:11px;padding:3px 0;border-top:1px dotted var(--line);gap:8px}
.card .row:first-of-type{border-top:none}
.card .label{color:var(--muted);letter-spacing:.5px;font-size:10px;text-transform:uppercase;white-space:nowrap}
.card .val{color:var(--fg);text-align:right;word-break:break-word}
.card .status{display:inline-block;padding:1px 6px;font-size:10px;letter-spacing:1px;border-radius:2px;background:var(--bg);color:var(--dim)}
.s-active{color:var(--ok)!important}
.s-stalled,.s-not-started,.s-critical,.s-missing{color:var(--err)!important}
.s-waiting,.s-active-unverified,.s-ready-to-ship,.s-warn{color:var(--warn)!important}
.s-complete,.s-parked{color:var(--muted)!important}
.s-ok{color:var(--ok)!important}
.card .next{color:var(--fg);font-size:12px;padding:8px 10px;background:var(--bg);border:1px solid var(--line);margin-top:8px;border-radius:2px;line-height:1.5}
.card .next::before{content:"\u2192 ";color:var(--accent)}
.card .blockers{margin-top:8px;font-size:11px;color:var(--err)}
.card .blockers .label{color:var(--err)}
.card .blocker-item::before{content:"\u26A0 "}
.card .stale{position:absolute;top:11px;right:12px;font-size:9px;color:var(--muted);letter-spacing:1px}
.card .stale.warn{color:var(--warn)}
.card .stale.err{color:var(--err)}
.empty{text-align:center;color:var(--muted);padding:40px 20px;font-size:12px}
.fleet-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.fleet-tile{background:var(--card);border:1px solid var(--line);padding:10px;border-radius:3px;text-align:center}
.fleet-tile .label{font-size:9px;color:var(--muted);letter-spacing:1px;text-transform:uppercase}
.fleet-tile .val{font-size:18px;color:var(--accent);font-weight:600;margin-top:4px}
.fleet-tile .sub{font-size:10px;color:var(--dim);margin-top:2px}
footer{position:fixed;bottom:0;left:0;right:0;background:var(--bg);border-top:1px solid var(--line);padding:7px 14px;font-size:10px;color:var(--muted);letter-spacing:1px;display:flex;justify-content:space-between;align-items:center;gap:10px}
footer .cmd{color:var(--accent);font-family:ui-monospace,monospace;font-size:10px}
</style>
</head><body>
<header>
  <div class="brand">LUMEN FLEET HEALTH</div>
  <h1 id="title">Lumen Health</h1>
  <div class="tabs">
    <span class="tab" data-s="projects">Projects <span class="badge" id="b-projects" style="display:none"></span></span>
    <span class="tab on" data-s="health">Health <span class="badge" id="b-health" style="display:none"></span></span>
    <span class="tab" data-s="fleet">Fleet</span>
  </div>
  <div class="filters" id="filters" style="display:none">
    <span class="filter on" data-f="all">all</span>
    <span class="filter" data-f="active">active</span>
    <span class="filter" data-f="stalled">needs attention</span>
    <span class="filter" data-f="tinywhale">tinywhale</span>
    <span class="filter" data-f="lumen">lumen</span>
  </div>
</header>
<main>
  <div class="section on" id="s-projects"><div class="empty">loading\u2026</div></div>
  <div class="section" id="s-health"><div class="empty">loading\u2026</div></div>
  <div class="section" id="s-fleet"><div class="empty">loading\u2026</div></div>
</main>
<div id="handoff-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:100;overflow-y:auto" onclick="if(event.target===this)this.style.display='none'">
  <div style="max-width:680px;margin:30px auto;padding:20px;background:var(--card);border:1px solid var(--accent);border-radius:4px">
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px">
      <span id="handoff-title" style="color:var(--accent);font-size:14px;letter-spacing:1px"></span>
      <span style="color:var(--dim);font-size:11px;cursor:pointer" onclick="document.getElementById('handoff-modal').style.display='none'">[ close ]</span>
    </div>
    <pre id="handoff-content" style="white-space:pre-wrap;color:var(--fg);font-size:12px;line-height:1.5;font-family:ui-monospace,monospace;margin:0">loading…</pre>
  </div>
</div>
<footer>
  <span id="count">\u2014</span>
  <span class="cmd" id="cmd">project-touch &lt;id&gt; --next "..."</span>
</footer>
<script>
let projData = null, healthData = null, currentSection = "projects";

function ago(dateStr){
  if(!dateStr) return "\u2014";
  const d = new Date(dateStr + "T12:00:00-07:00");
  const days = Math.floor((Date.now() - d.getTime())/86400000);
  if (days < 1) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return days + "d ago";
  if (days < 30) return Math.floor(days/7) + "w ago";
  return Math.floor(days/30) + "mo ago";
}
function staleClass(dateStr){
  if(!dateStr) return "err";
  const d = new Date(dateStr + "T12:00:00-07:00");
  const days = Math.floor((Date.now() - d.getTime())/86400000);
  if (days > 21) return "err";
  if (days > 7)  return "warn";
  return "";
}
function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}

function renderProjects(){
  if (!projData) return;
  const f = document.querySelector("#filters .filter.on").dataset.f;
  const list = projData.projects
    .filter(p => {
      if (f === "all") return true;
      if (f === "active") return p.status === "active" || p.status === "active-unverified";
      if (f === "stalled") return p.health === "red" || p.status === "stalled";
      if (f === "tinywhale") return p.brand === "tinywhale";
      if (f === "lumen") return p.brand === "lumen";
      return true;
    })
    .sort((a,b) => {
      const ho = {red:0, yellow:1, green:2};
      if (ho[a.health] !== ho[b.health]) return ho[a.health] - ho[b.health];
      return (b.last_touched || "").localeCompare(a.last_touched || "");
    });
  const m = document.getElementById("s-projects");
  if (list.length === 0) { m.innerHTML = '<div class="empty">nothing here</div>'; return; }
  m.innerHTML = list.map(p => `
    <div class="card h-${p.health}">
      <div class="stale ${staleClass(p.last_touched)}">${ago(p.last_touched)}</div>
      <div class="name">${escapeHtml(p.name)}<span class="brand-tag brand-${p.brand}">${p.brand}</span></div>
      <div class="summary">${escapeHtml(p.summary || "")}</div>
      <div class="row"><span class="label">status</span><span class="val status s-${p.status}">${p.status}</span></div>
      <div class="row"><span class="label">id</span><span class="val">${p.id}</span></div>
      ${p.next_step ? `<div class="next">${escapeHtml(p.next_step)}</div>` : ""}
      ${(p.blockers && p.blockers.length) ? `<div class="blockers"><span class="label">blockers</span>${p.blockers.map(b=>`<div class="blocker-item">${escapeHtml(b)}</div>`).join("")}</div>` : ""}
      <div class="row" style="margin-top:8px;border-top:1px dotted var(--line);padding-top:8px"><span class="label">handoff</span><span class="val"><a href="#" data-pid="${p.id}" class="handoff-link" style="color:var(--accent);text-decoration:none">view log →</a></span></div>
    </div>
  `).join("");
  document.getElementById("count").textContent = `${list.length} / ${projData.projects.length} shown`;
  // Red badge for needs-attention
  const red = projData.projects.filter(p => p.health === "red" || p.status === "stalled").length;
  const b = document.getElementById("b-projects");
  if (red > 0) { b.textContent = red; b.style.display = ""; } else { b.style.display = "none"; }
}

function renderHealth(){
  if (!healthData) return;
  const m = document.getElementById("s-health");
  const checks = (healthData.checks || []).map(c => `
    <div class="card h-${c.status}">
      <div class="name">${escapeHtml(c.name)}</div>
      <div class="summary">${escapeHtml(c.desc)}</div>
      <div class="row"><span class="label">status</span><span class="val status s-${c.status}">${c.status}</span></div>
      <div class="row"><span class="label">detail</span><span class="val">${escapeHtml(c.detail)}</span></div>
    </div>
  `).join("");
  const containers = (healthData.containers || []).map(c => `
    <div class="card h-${c.status}">
      <div class="name">📦 ${escapeHtml(c.name)}</div>
      <div class="row"><span class="label">status</span><span class="val status s-${c.status}">${c.status}</span></div>
      <div class="row"><span class="label">detail</span><span class="val">${escapeHtml(c.detail)}</span></div>
    </div>
  `).join("");
  m.innerHTML = checks + containers || '<div class="empty">no checks</div>';

  // Red badge
  const red = (healthData.checks||[]).filter(c => c.status === "critical" || c.status === "missing").length
            + (healthData.containers||[]).filter(c => c.status === "missing").length;
  const b = document.getElementById("b-health");
  if (red > 0) { b.textContent = red; b.style.display = ""; } else { b.style.display = "none"; }
}

function renderFleet(){
  if (!healthData) return;
  const f = healthData.fleet || {};
  const m = document.getElementById("s-fleet");
  if (!f.hostname) { m.innerHTML = '<div class="empty">fleet endpoint unreachable</div>'; return; }
  const aq = healthData.article_queue || {inbox:0,done:0,failed:0};
  m.innerHTML = `
    <div class="fleet-grid">
      <div class="fleet-tile"><div class="label">CPU</div><div class="val">${f.cpu_pct||0}%</div></div>
      <div class="fleet-tile"><div class="label">RAM</div><div class="val">${f.ram_pct||0}%</div></div>
      <div class="fleet-tile"><div class="label">DISK</div><div class="val">${f.disk_pct||0}%</div></div>
      <div class="fleet-tile"><div class="label">UPTIME</div><div class="val" style="font-size:14px">${escapeHtml(f.uptime||'')}</div></div>
      <div class="fleet-tile"><div class="label">SERVICES</div><div class="val">${f.services_active||0}/${f.services_total||0}</div></div>
      <div class="fleet-tile"><div class="label">CONTAINERS</div><div class="val">${f.containers||0}</div></div>
      <div class="fleet-tile"><div class="label">ARTICLE INBOX</div><div class="val">${aq.inbox}</div><div class="sub">${aq.done} done / ${aq.failed} fail</div></div>
    </div>
    <div class="card h-ok">
      <div class="name">${escapeHtml(f.hostname)}</div>
      <div class="row"><span class="label">load</span><span class="val">${(f.load||[]).join(' ')}</span></div>
    </div>
  `;
}

async function fetchAll(){
  try {
    const [p, h] = await Promise.all([
      fetch("/api/projects", {cache:"no-store"}).then(r => r.json()),
      fetch("/api/health",   {cache:"no-store"}).then(r => r.json()),
    ]);
    projData = p; healthData = h;
    renderProjects(); renderHealth(); renderFleet();
  } catch(e) { console.error(e); }
}

// Handoff modal open
document.addEventListener("click", async (e) => {
  const a = e.target.closest(".handoff-link");
  if (!a) return;
  e.preventDefault();
  const pid = a.dataset.pid;
  document.getElementById("handoff-title").textContent = pid + " / handoff log";
  document.getElementById("handoff-content").textContent = "loading…";
  document.getElementById("handoff-modal").style.display = "";
  try {
    const r = await fetch("/api/handoff/" + encodeURIComponent(pid));
    document.getElementById("handoff-content").textContent = await r.text();
  } catch (err) {
    document.getElementById("handoff-content").textContent = "error: " + err;
  }
});

// Tab switching
document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("on"));
    document.querySelectorAll(".section").forEach(x => x.classList.remove("on"));
    t.classList.add("on");
    const s = t.dataset.s;
    document.getElementById("s-" + s).classList.add("on");
    document.getElementById("filters").style.display = (s === "projects") ? "" : "none";
    document.getElementById("title").textContent = s.charAt(0).toUpperCase() + s.slice(1);
    currentSection = s;
  });
});
document.querySelectorAll("#filters .filter").forEach(el => {
  el.addEventListener("click", () => {
    document.querySelectorAll("#filters .filter").forEach(x => x.classList.remove("on"));
    el.classList.add("on");
    renderProjects();
  });
});
fetchAll();
setInterval(fetchAll, 30000);
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/projects":
            try:
                self._send(200, PROJECTS_FILE.read_bytes(), "application/json")
            except Exception as e:
                self._send(500, {"error": str(e), "projects": []})
        elif self.path == "/api/health":
            try:
                self._send(200, health_snapshot())
            except Exception as e:
                self._send(500, {"error": str(e), "checks": [], "containers": [], "fleet": {}})
        elif self.path.startswith("/api/handoff/"):
            pid = self.path.split("/api/handoff/", 1)[1]
            # wq#2751: pid came straight off the raw path, so ../../kb/ISSUE-LOG read any *.md
            if not HANDOFF_ID.fullmatch(pid):
                self._send(404, {"error": "not found"})
                return
            hf = HANDOFF_DIR / f"{pid}.md"
            if hf.exists():
                self._send(200, hf.read_text(), "text/plain; charset=utf-8")
            else:
                self._send(404, "no handoff yet for " + pid, "text/plain; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    # wq#2751: loopback only; projects.lumennode.uk (CF Access) reaches it via cloudflared at localhost
    host = os.environ.get("PROJECTS_APP_BIND", "127.0.0.1")
    port = int(os.environ.get("PROJECTS_APP_PORT", "8124"))
    srv = HTTPServer((host, port), Handler)
    print(f"projects-app v2 listening on {host}:{port}")
    srv.serve_forever()
