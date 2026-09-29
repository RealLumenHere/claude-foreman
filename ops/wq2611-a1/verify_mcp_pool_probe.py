#!/usr/bin/env python3
"""wq#2611 synthetic verification (run once, read-only against everything except
a scratch AQ_DB under /tmp/fworker-wq2611/): drives the real
sentinel_probes.probe_mcp_pool_degraded() + the real main()-loop FAIL/PASS
handling (aq ingest, edge-clear, ntfy_urgent/ntfy_recovered) against a local
HTTP stand-in for mcpw.lumennode.uk/pool.txt (the live Worker route does not
exist yet -- see the probe's own docstring / design doc Sec3.1/Sec6). Proves:
one degrade cycle -> exactly one open aq row + exactly one urgent ntfy send;
a second FAIL while still degraded -> aq row bumps (count=2), NO second ntfy
(page-suppression, not per-minute spam); recovery -> aq row resolved + exactly
one recovered ntfy send.

Does not touch the live alert-queue db (AQ_DB override) or real ntfy/Gatus
(sentinel_probes._http intercepted for both destinations before any network
call is made).
"""
import http.server
import json
import os
import sys
import threading
import time

SCRATCH = "/tmp/fworker-wq2611"
os.makedirs(SCRATCH, exist_ok=True)
AQ_DB_PATH = os.path.join(SCRATCH, "wq2611_verify_alerts.db")
if os.path.exists(AQ_DB_PATH):
    os.remove(AQ_DB_PATH)
os.environ["AQ_DB"] = AQ_DB_PATH
STATE_PATH = os.path.join(SCRATCH, "wq2611_verify_state.json")
if os.path.exists(STATE_PATH):
    os.remove(STATE_PATH)

_secret_name = "MCPW_POOL_SHARED_SECRET"
_secret_val = "verify-" + str(os.getpid())
os.environ[_secret_name] = _secret_val


class _PoolHandler(http.server.BaseHTTPRequestHandler):
    healthy_count = 3

    def do_GET(self):
        if self.headers.get("X-Pool-Shared-Secret") != os.environ[_secret_name]:
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({"healthy_count": _PoolHandler.healthy_count}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), _PoolHandler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
# Must be set BEFORE sentinel_probes is imported: MCPW_POOL_URL is bound from
# this env var at module import time (os.environ.get(...) at module scope).
os.environ["SENTINEL_MCPW_POOL_URL"] = f"http://127.0.0.1:{port}/pool.txt"

sys.path.insert(0, "/home/lumen/sentinel/probes")
sys.path.insert(0, "/home/lumen/agents/alert-queue")
import sentinel_probes as sp
import aq

sp.STATE = __import__("pathlib").Path(STATE_PATH)

# Intercept only the two real network sinks main() would otherwise hit
# (NTFY page channel, Gatus dashboard push); the pool GET above goes through
# unmodified so the probe body itself is exercised for real.
orig_http = sp._http
counts = {"urgent": 0, "recovered": 0, "gatus_push": 0}


def _fake_http(url, data=None, headers=None, timeout=20, method=None):
    if url == sp.NTFY:
        title = (headers or {}).get("Title", "")
        if title.startswith("SENTINEL CRITICAL"):
            counts["urgent"] += 1
        elif title.startswith("SENTINEL RECOVERED"):
            counts["recovered"] += 1
        return 200, "ok"
    if url.startswith(sp.GATUS):
        counts["gatus_push"] += 1
        return 200, "ok"
    return orig_http(url, data=data, headers=headers, timeout=timeout, method=method)


sp._http = _fake_http

PROBE_NAME = "probe-mcp-pool-degraded"
FN = sp.probe_mcp_pool_degraded


def run_one_cycle():
    """Replicates sentinel_probes.main()'s per-probe body for just this probe,
    without looping every other probe in PROBES (those hit real litellm/maat/etc
    endpoints this sandbox shouldn't reach)."""
    state = sp._state()
    fires = state.setdefault("_fires", {})
    ok, detail, critical = sp._run_probe(FN)
    if not ok and critical:
        time.sleep(0.1)  # stand-in for RETRY_SETTLE_S, kept short for this verify run
        ok2, detail2, critical2 = sp._run_probe(FN)
        if ok2:
            ok, detail, critical = True, f"transient cleared: {detail2}", False
        else:
            ok, detail, critical = ok2, detail2, (critical2 or critical)
    sp.push_gatus(PROBE_NAME, ok, detail)
    if ok:
        sp._edge_clear(PROBE_NAME)
        f = fires.pop(PROBE_NAME, None)
        if f is not None:
            sp.ntfy_recovered(PROBE_NAME, f)
    else:
        aq.ingest(source="probe", key=PROBE_NAME, title=f"probe FAIL: {PROBE_NAME}",
                  message=detail, priority="high" if critical else "default")
        if critical:
            ts = time.time()
            f = fires.get(PROBE_NAME)
            if f is None:
                fires[PROBE_NAME] = {"since": ts, "paged_at": ts, "pages": 1}
                sp.ntfy_urgent(f"SENTINEL CRITICAL: {PROBE_NAME}", detail)
            elif ts - f.get("paged_at", 0) >= sp.REPAGE_S:
                f["paged_at"] = ts
                sp.ntfy_urgent(f"SENTINEL CRITICAL (still failing): {PROBE_NAME}", detail)
    sp._save(state)
    return ok, detail, critical


def open_rows():
    c = aq.db()
    n = c.execute("SELECT COUNT(*) FROM alerts WHERE source='probe' AND key=? AND status='open'",
                  (PROBE_NAME,)).fetchone()[0]
    cnt = c.execute("SELECT count FROM alerts WHERE source='probe' AND key=? AND status='open'",
                     (PROBE_NAME,)).fetchone()
    c.close()
    return n, (cnt[0] if cnt else None)


def main():
    fails = []

    # cycle 1: degrade (healthy_count=1 < 3)
    _PoolHandler.healthy_count = 1
    ok, detail, critical = run_one_cycle()
    n_open, count = open_rows()
    print(f"[cycle1 degrade] ok={ok} critical={critical} detail={detail!r} "
          f"open_rows={n_open} row_count={count} urgent_sent={counts['urgent']}")
    if ok is not False or critical is not True:
        fails.append("cycle1: probe did not FAIL+critical on healthy_count=1")
    if n_open != 1:
        fails.append(f"cycle1: expected exactly 1 open aq row, got {n_open}")
    if counts["urgent"] != 1:
        fails.append(f"cycle1: expected exactly 1 urgent ntfy send, got {counts['urgent']}")

    # cycle 2: still degraded (simulates the next 30-min sentinel cycle) -- must
    # bump the SAME row, not create a second one, and must NOT re-page (no
    # per-minute spam; REPAGE_S default 6h has not elapsed).
    ok, detail, critical = run_one_cycle()
    n_open, count = open_rows()
    print(f"[cycle2 still-degraded] ok={ok} critical={critical} "
          f"open_rows={n_open} row_count={count} urgent_sent={counts['urgent']}")
    if n_open != 1:
        fails.append(f"cycle2: expected still exactly 1 open aq row (dedup), got {n_open}")
    if count != 2:
        fails.append(f"cycle2: expected row count bumped to 2, got {count}")
    if counts["urgent"] != 1:
        fails.append(f"cycle2: expected urgent ntfy STILL at 1 (page-suppressed), got {counts['urgent']}")

    # cycle 3: recover (healthy_count=3)
    _PoolHandler.healthy_count = 3
    ok, detail, critical = run_one_cycle()
    n_open, count = open_rows()
    print(f"[cycle3 recover] ok={ok} critical={critical} detail={detail!r} "
          f"open_rows={n_open} recovered_sent={counts['recovered']}")
    if ok is not True:
        fails.append("cycle3: probe did not PASS on healthy_count=3")
    if n_open != 0:
        fails.append(f"cycle3: expected 0 open aq rows after edge-clear, got {n_open}")
    if counts["recovered"] != 1:
        fails.append(f"cycle3: expected exactly 1 recovered ntfy send, got {counts['recovered']}")

    server.shutdown()

    if fails:
        print("FAIL:")
        for f in fails:
            print(" -", f)
        sys.exit(1)
    print("PASS: one degrade -> exactly one aq row + one urgent ntfy; "
          "repeat degrade -> same row bumped, no re-page; "
          "recovery -> row resolved + exactly one recovered ntfy.")


if __name__ == "__main__":
    main()
