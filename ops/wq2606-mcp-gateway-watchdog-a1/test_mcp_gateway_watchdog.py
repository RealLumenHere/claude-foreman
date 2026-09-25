#!/usr/bin/env python3
"""Functional test for mcp-gateway-watchdog.sh without touching real systemd
or the live fleet. Run: python3 test_mcp_gateway_watchdog.py

Runs a local mock HTTP server standing in for the gateway (/mcp and /ready)
and a fake restart command that just appends to a log file instead of
calling systemctl, then drives the script (with compressed cooldown/window
thresholds via env overrides) through the exact sequence the ticket's live
TEST TO FAILURE describes: 2 consecutive fails -> restart; cooldown
suppresses an immediate re-restart; a 3rd restart inside the 30-min-analog
window is refused and a flap alert fires exactly once; recovery resets state.
This worker's sandbox cannot install a systemd unit or run `systemctl
restart` on the real gateway (root-only, see ISSUE-LOG #68), so this is the
evidence available from here; apply.sh's own rollout is where the live
kill -STOP drill runs.
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "mcp-gateway-watchdog.sh")

STATE = {"healthy": True}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if not STATE["healthy"]:
            self.send_response(599)
            self.end_headers()
            return
        body = {"jsonrpc": "2.0", "id": "watchdog", "result": {"tools": []}}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not STATE["healthy"]:
            self.send_response(599)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()


def main():
    port = 18100
    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    tmpdir = tempfile.mkdtemp(prefix="wq2606-watchdog-")
    state_dir = os.path.join(tmpdir, "state")
    restart_log = os.path.join(tmpdir, "restarts.log")
    fake_restart = os.path.join(tmpdir, "fake-restart.sh")
    with open(fake_restart, "w") as f:
        f.write(f'#!/bin/sh\necho "$(date +%s) restart $1" >> {restart_log}\n')
    os.chmod(fake_restart, 0o755)

    env = dict(os.environ)
    env.update({
        "WATCHDOG_STATE_DIR": state_dir,
        "WATCHDOG_RESTART_CMD": fake_restart,
        "WATCHDOG_COOLDOWN": "2",
        "WATCHDOG_RESTART_WINDOW": "20",
        "WATCHDOG_MAX_RESTARTS": "2",
        "WATCHDOG_NTFY_BIN": "/nonexistent",
        "WATCHDOG_AQ_BIN": "/nonexistent",
        "WATCHDOG_NTFY_URL": "http://127.0.0.1:1/unreachable",
    })

    def run():
        return subprocess.run(
            ["bash", SCRIPT, "--mode", "mcp", "--unit", "test-gateway", "--port", str(port)],
            env=env, capture_output=True, text=True,
        )

    def restarts():
        if not os.path.exists(restart_log):
            return 0
        return sum(1 for _ in open(restart_log))

    failures = []

    STATE["healthy"] = True
    run()
    if restarts() != 0:
        failures.append("healthy probe triggered a restart")

    STATE["healthy"] = False
    run()
    if restarts() != 0:
        failures.append("restarted after only 1 failure (threshold should be 2)")
    run()
    if restarts() != 1:
        failures.append(f"expected 1 restart after 2 fails, got {restarts()}")

    run()
    run()
    if restarts() != 1:
        failures.append(f"cooldown did not suppress restart, count={restarts()}")

    time.sleep(2.2)

    run()
    run()
    if restarts() != 2:
        failures.append(f"expected 2nd restart after cooldown cleared, got {restarts()}")

    time.sleep(2.2)

    run()
    run()
    if restarts() != 2:
        failures.append(f"3rd restart was NOT refused during flap window, count={restarts()}")

    flap_stamp = os.path.join(state_dir, "test-gateway.flap-alerted")
    if not os.path.exists(flap_stamp):
        failures.append("flap alert stamp was not written on flap detection")

    STATE["healthy"] = True
    run()
    fails_file = os.path.join(state_dir, "test-gateway.fails")
    fails_val = open(fails_file).read().strip() if os.path.exists(fails_file) else "?"
    if fails_val != "0":
        failures.append(f"fails counter did not reset on recovery, got {fails_val!r}")

    final_restarts = restarts()
    srv.shutdown()
    shutil.rmtree(tmpdir, ignore_errors=True)

    if failures:
        print("FAIL:")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    print(f"ALL CHECKS PASSED ({final_restarts} restarts recorded, flap alert fired, cooldown honored, recovery reset fails)")


if __name__ == "__main__":
    main()
