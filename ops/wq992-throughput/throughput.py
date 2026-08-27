#!/usr/bin/env python3
"""wq#992 throughput metric — tickets/hour the fleet actually finishes.

The R4 intervention ledger answers "is the fleet needing Frank *less*?" (events/week,
down = good). This answers the complementary production question the rapid-chaining work
(wq#992 A/B/C) exists to move: "how many tickets/hour is the fleet closing, versus the
~7/hr Frank hits by hand?" (up = good). Kept as its own module rather than bolted onto
the ledger so the ledger's one-number thesis stays clean; it emits its own telemetry and
is meant to ride alongside R4 on Glance (see README).

Read-only against the work-queue DB. Emits telemetry JSON atomically. No writes anywhere
except the telemetry/Glance asset files.
"""
import argparse
import datetime
import json
import os
import sqlite3
import tempfile

QUEUE = "/home/lumen/agents/work-queue/queue.db"
TELEM_OUT = "/home/lumen/telemetry/throughput.json"
GLANCE_OUT = "/home/lumen/glance/assets/throughput.json"

# Frank's observed manual rate: ~40 tickets in a ~6h sitting. The bar wq#992 is trying to
# clear with the dispatcher instead of Frank's hands.
BAR_PER_HOUR = 7.0

# (label, hours) — trailing rolling windows. 24h is the headline: long enough to smooth
# spawn bursts, recent enough to reflect the current dispatcher config.
WINDOWS = [("1h", 1), ("6h", 6), ("24h", 24), ("7d", 168)]
HEADLINE_WINDOW = "24h"


def _fmt(dt):
    # matches queue.db done_at: 'YYYY-MM-DD HH:MM:SS' in UTC
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def ro(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def compute(conn, now):
    """Pure: given an open connection and a UTC `now` datetime, return the metric dict.
    Tested against a synthetic tasks table — no dependency on the live DB."""
    windows = {}
    for label, hrs in WINDOWS:
        cutoff = _fmt(now - datetime.timedelta(hours=hrs))
        n = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='done' AND done_at IS NOT NULL "
            "AND done_at >= ?", (cutoff,)).fetchone()[0]
        windows[label] = {"done": n, "per_hour": round(n / hrs, 2), "hours": hrs}

    cutoff24 = _fmt(now - datetime.timedelta(hours=24))
    # peak single clock-hour in the last 24h — the fleet's demonstrated burst ceiling.
    peak_row = conn.execute(
        "SELECT substr(done_at,1,13) hr, COUNT(*) c FROM tasks "
        "WHERE status='done' AND done_at IS NOT NULL AND done_at >= ? "
        "GROUP BY hr ORDER BY c DESC, hr DESC LIMIT 1", (cutoff24,)).fetchone()
    peak = ({"hour_utc": peak_row["hr"], "done": peak_row["c"]}
            if peak_row else {"hour_utc": None, "done": 0})

    lanes = {r["lane"]: r["c"] for r in conn.execute(
        "SELECT lane, COUNT(*) c FROM tasks WHERE status='done' AND done_at IS NOT NULL "
        "AND done_at >= ? GROUP BY lane ORDER BY c DESC", (cutoff24,)).fetchall()}

    headline = windows[HEADLINE_WINDOW]["per_hour"]
    return {
        "headline_per_hour": headline,
        "headline_window": HEADLINE_WINDOW,
        "bar_to_beat_per_hour": BAR_PER_HOUR,
        "beating_bar": headline >= BAR_PER_HOUR,
        "pct_of_bar": round(100 * headline / BAR_PER_HOUR, 1),
        "windows": windows,
        "peak_hour_24h": peak,
        "lanes_24h": lanes,
        "generated_at_utc": _fmt(now),
        "tz": "UTC",
        "note": "tickets/hour the fleet closed (status=done). bar=Frank's ~7/hr manual "
                "rate. headline=trailing 24h. up=good. wq#992 rapid-chaining target.",
    }


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def emit(data):
    _atomic_write(TELEM_OUT, data)
    # Glance ride-along is best-effort: if the assets dir isn't present, telemetry still
    # landed and that is the source of record.
    try:
        _atomic_write(GLANCE_OUT, data)
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description="fleet throughput (tickets/hour) metric")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="compute and emit telemetry")
    sub.add_parser("show", help="print the headline only, don't emit")
    args = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    with ro(QUEUE) as conn:
        data = compute(conn, now)

    if args.cmd == "run":
        emit(data)
        print(json.dumps({"headline_per_hour": data["headline_per_hour"],
                          "bar_to_beat_per_hour": data["bar_to_beat_per_hour"],
                          "beating_bar": data["beating_bar"]}, indent=1))
    else:
        print(f"{data['headline_per_hour']} tickets/hr "
              f"({data['pct_of_bar']}% of the {BAR_PER_HOUR}/hr bar)")


if __name__ == "__main__":
    main()
