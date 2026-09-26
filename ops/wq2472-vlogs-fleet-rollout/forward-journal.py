#!/usr/bin/env python3
# wq#857 / wq#2051 -- journald -> VictoriaLogs forwarder (Python; name kept so the unit's ExecStart is unchanged).
# Runs as a systemd oneshot on a 60s timer.
#
# wq#2051 fixes vs the original bash version:
#   1. Cursor now advances to the __CURSOR of the LAST ENTRY ACTUALLY SHIPPED. The old script set it to the
#      journal tail (`journalctl -n 1`) after the POST, so everything logged while the tick ran, and everything
#      past MAX_LINES on an overflow tick, was skipped permanently.
#   2. One process for the whole tick. The old script forked python3 once per line (~40ms each, 5-8s CPU/tick).
#   3. Drains a backlog in chunks (CHUNK lines per POST) until it catches up or MAX_TOTAL/TIME_BUDGET is hit,
#      then stops; the cursor is persisted after every successful chunk so the next tick resumes exactly there.
#   4. A failed POST leaves the cursor untouched (chunk is re-sent next tick).
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

VLOGS_URL = os.environ.get("VLOGS_URL", "http://100.110.156.91:9428")
CURSOR_FILE = os.environ.get("CURSOR_FILE", "/var/lib/vlogs-forwarder/cursor")
CHUNK = int(os.environ.get("CHUNK", "2000"))            # lines per POST
MAX_TOTAL = int(os.environ.get("MAX_TOTAL", "50000"))   # lines per tick
TIME_BUDGET = float(os.environ.get("TIME_BUDGET", "45"))  # seconds per tick (timer period is 60s)
HOSTNAME_TAG = os.uname().nodename


def read_cursor():
    try:
        with open(CURSOR_FILE) as f:
            c = f.read().strip()
        return c or None
    except FileNotFoundError:
        return None


def write_cursor(cursor):
    os.makedirs(os.path.dirname(CURSOR_FILE), exist_ok=True)
    tmp = CURSOR_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(cursor + "\n")
    os.replace(tmp, CURSOR_FILE)


def as_text(v):
    if isinstance(v, str):
        return v
    if isinstance(v, list):  # journald encodes non-UTF8 MESSAGE as a byte array
        try:
            return bytes(v).decode("utf-8", "replace")
        except Exception:
            return str(v)
    return "" if v is None else str(v)


def convert(j):
    ts_us = int(j.get("__REALTIME_TIMESTAMP", "0"))
    return json.dumps({
        "_msg": as_text(j.get("MESSAGE", "")),
        "_time": datetime.fromtimestamp(ts_us / 1e6, tz=timezone.utc).isoformat(),
        "host": as_text(j.get("_HOSTNAME", HOSTNAME_TAG)),
        "unit": as_text(j.get("_SYSTEMD_UNIT", "")),
        "syslog_id": as_text(j.get("SYSLOG_IDENTIFIER", "")),
        "priority": as_text(j.get("PRIORITY", "")),
        "pid": as_text(j.get("_PID", "")),
    })


def post(lines):
    body = ("\n".join(lines) + "\n").encode()
    req = urllib.request.Request(VLOGS_URL + "/insert/jsonline", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def main():
    cursor = read_cursor()
    args = ["journalctl", "-o", "json", "--no-pager"]
    args += ["--after-cursor=" + cursor] if cursor else ["--since=-1h"]
    # NOTE: no -n. With --after-cursor journalctl emits oldest-first from the cursor, so we read a stream from the
    # cursor and stop ourselves at MAX_TOTAL / TIME_BUDGET instead of letting -n pick the window.
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
    start = time.monotonic()
    shipped = 0
    batch, last_cursor = [], None
    stop = False

    def flush():
        nonlocal batch, shipped
        if not batch:
            return True
        code = post(batch)
        if code not in (200, 204):
            print("[vlogs-forwarder] POST failed: HTTP %s (shipped %d this tick, cursor kept at last good chunk)"
                  % (code, shipped), file=sys.stderr)
            return False
        write_cursor(last_cursor)
        shipped += len(batch)
        batch = []
        return True

    try:
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                j = json.loads(raw)
                out = convert(j)
                cur = j["__CURSOR"]
            except Exception:
                continue  # unparseable entry: skip, cursor moves on with the next good one
            batch.append(out)
            last_cursor = cur
            if len(batch) >= CHUNK:
                if not flush():
                    proc.kill()
                    sys.exit(1)
                if shipped >= MAX_TOTAL or time.monotonic() - start > TIME_BUDGET:
                    stop = True
                    break
        if not stop and not flush():
            sys.exit(1)
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait()

    if shipped:
        print("[vlogs-forwarder] Shipped %d lines%s" % (shipped, " (backlog remains, resuming next tick)" if stop else ""))
    else:
        print("[vlogs-forwarder] No new entries")


if __name__ == "__main__":
    main()
