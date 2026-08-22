#!/usr/bin/env python3
"""dashboard.py — read-only foreman dispatch dashboard HTTP server (wq#718 AUTO-6).

Stdlib-only (no new dependency — matches the rest of this repo's http/urllib-only
convention, e.g. foreman_dispatcher.py's Langfuse client). GET-only, two routes:

  GET /              minimal HTML render of dashboard_data.snapshot()
  GET /api/snapshot  the same snapshot as JSON

READ-ONLY BY CONSTRUCTION: dashboard_data.py never claims/releases/blocks a ticket,
writes a state file, or spawns anything — see its module docstring. This server adds
no POST/PUT/DELETE handler at all, so there is no code path that could turn it into a
control surface even by future accident; that's the guardrail ("dashboard is
READ-ONLY — never a control surface").

Deploy posture matches AUTO-3/AUTO-7: this file runs directly (`python3 dashboard.py
--port 8791`) for local/manual use; dispatcher/systemd/lumen-foreman-dashboard.service
ships STAGED (repo-only, disabled) for Frank to enable. Making it reachable at
dispatch.lumennode.uk behind Cloudflare Access is DNS + Cloudflare account work this
worker has no access to — see systemd/INSTALL.md's wq#718 section for the documented
steps Frank runs by hand.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DISPATCHER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, DISPATCHER_DIR)
import dashboard_data  # noqa: E402

DEFAULT_PORT = int(os.environ.get("FOREMAN_DASHBOARD_PORT", "8791"))


def _fmt_ts(epoch):
    if epoch is None:
        return "?"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_dur(seconds):
    if seconds is None:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def render_html(snap):
    rows_running = "".join(
        f"<tr><td>wq{w['ticket']}</td><td>{w['lane']}</td><td>{w['model']}</td>"
        f"<td>{_fmt_ts(w['started_at'])}</td><td>{_fmt_dur(w['elapsed_s'])}</td>"
        f"<td>{_fmt_ts(w['projected_end'])}</td></tr>"
        for w in snap["running"].get("workers", []))
    rows_queue = "".join(
        f"<tr><td>{lane}</td><td>{d['open']}</td><td>{d['eligible']}</td>"
        f"<td>{d['blocked']}</td></tr>"
        for lane, d in sorted(snap["queue_depth"].items()))
    fr = snap["failure_rate"]["overall"]
    cb = snap["circuit_breaker"]
    stuck = snap["stuck_tickets"]
    rows_stuck = "".join(
        f"<tr><td>wq{tid}</td><td>{d['lane']}</td><td>{d['attempts']}</td>"
        f"<td>{d['status']}</td></tr>" for tid, d in stuck.items())
    armed_label = "ARMED" if cb["armed"] else "DISARMED"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>foreman dispatch — mission control</title>
<meta http-equiv="refresh" content="30">
<style>
body {{ font-family: monospace; margin: 2rem; background: #111; color: #ddd; }}
h2 {{ border-bottom: 1px solid #444; padding-bottom: .3rem; }}
table {{ border-collapse: collapse; margin-bottom: 2rem; }}
td, th {{ padding: .25rem .75rem; border: 1px solid #333; text-align: left; }}
.armed {{ color: #6f6; }} .disarmed {{ color: #f66; font-weight: bold; }}
</style></head><body>
<h1>foreman dispatch — mission control</h1>
<p>generated {snap['generated_at']} — read-only, refreshes every 30s</p>

<h2>circuit breaker: <span class="{'armed' if cb['armed'] else 'disarmed'}">{armed_label}</span></h2>
<p>{cb['failures']}/{cb['total']} catastrophic-class attempts in the last
{cb['window_minutes']}m (rate {cb['rate']:.0%})</p>

<h2>currently running ({len(snap['running'].get('workers', []))})</h2>
<table><tr><th>ticket</th><th>lane</th><th>model</th><th>started</th><th>elapsed</th>
<th>projected end</th></tr>{rows_running}</table>

<h2>queue depth by lane</h2>
<table><tr><th>lane</th><th>open</th><th>eligible</th><th>blocked</th></tr>
{rows_queue}</table>

<h2>failure rate (overall, broad "not success")</h2>
<p>24h: {fr['24h']['failures']}/{fr['24h']['total']} ({fr['24h']['rate']:.0%}) —
7d: {fr['7d']['failures']}/{fr['7d']['total']} ({fr['7d']['rate']:.0%})</p>

<h2>stuck / runaway tickets ({len(stuck)})</h2>
<table><tr><th>ticket</th><th>lane</th><th>attempts</th><th>status</th></tr>
{rows_stuck}</table>

<h2>budget</h2>
<pre>{json.dumps(snap['budget'], indent=2)}</pre>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[dashboard] " + (fmt % args) + "\n")

    def _send(self, code, body, content_type):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path not in ("/", "/api/snapshot"):
            self._send(404, "not found", "text/plain")
            return
        try:
            snap = dashboard_data.snapshot()
        except Exception as e:  # noqa: BLE001 — a data-gathering hiccup must 500, not hang
            self._send(500, f"snapshot failed: {e!r}", "text/plain")
            return
        if self.path == "/api/snapshot":
            self._send(200, json.dumps(snap, indent=2), "application/json")
        else:
            self._send(200, render_html(snap), "text/html; charset=utf-8")

    # No do_POST/do_PUT/do_DELETE: GET-only by construction (see module docstring).


def main(argv=None):
    ap = argparse.ArgumentParser(description="foreman dispatch dashboard (wq#718)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--bind", default="127.0.0.1",
                    help="loopback by default — a reverse proxy (Cloudflare Access) "
                         "terminates the public side; see systemd/INSTALL.md")
    a = ap.parse_args(argv)
    server = ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"[dashboard] serving on {a.bind}:{a.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
