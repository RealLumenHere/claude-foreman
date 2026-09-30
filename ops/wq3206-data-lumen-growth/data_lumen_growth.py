#!/usr/bin/env python3
"""wq#3206 — daily growth tracker for /data/lumen (md0).

/data/lumen was found at 97% with no history to say whether that is a slow
fill or a runaway (overnight-1 health sweep, 2026-09-30). wq#3171 shipped the
raise/clear alarm at 90% (ops/wq3171-disk-data-lumen-alert); that is
edge-triggered and answers "is it over the line now", not "how fast is it
filling". This script answers the second question: append one row/day to a
TSV log, then report the trailing slope in GB/day so a human (or the next
alarm) can tell "15G free, filling at 0.1G/day" (months of runway) from "15G
free, filling at 3G/day" (days).

Usage:
    data_lumen_growth.py record            # append today's df snapshot
    data_lumen_growth.py slope [--days N]  # GB/day over the trailing N days (default 30)
"""
import argparse
import csv
import os
import shutil
import sys
import time

MOUNT = os.environ.get("DATA_LUMEN_GROWTH_MOUNT", "/data/lumen")
LOG_PATH = os.environ.get(
    "DATA_LUMEN_GROWTH_LOG", "/home/lumen/state/data-lumen-growth/growth.tsv"
)
FIELDS = ("ts", "used_bytes", "total_bytes", "pct")


def snapshot(path=MOUNT):
    """(used_bytes, total_bytes, pct) for `path`, or None if it can't be read
    (missing mount, permission) — never raises, matching disk_pct_alarm.py's
    fail-forward probe contract."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    if usage.total == 0:
        return None
    pct = round(100.0 * usage.used / usage.total, 2)
    return usage.used, usage.total, pct


def record(log_path=LOG_PATH, path=MOUNT, now=None):
    snap = snapshot(path)
    if snap is None:
        print(f"record: could not read {path}, nothing logged", file=sys.stderr)
        return 1
    used, total, pct = snap
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    is_new = not os.path.exists(log_path)
    with open(log_path, "a", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        if is_new:
            w.writerow(FIELDS)
        w.writerow([int(now or time.time()), used, total, pct])
    print(f"record: {path} {pct}% ({used}/{total} bytes) -> {log_path}")
    return 0


def _read_rows(log_path):
    if not os.path.exists(log_path):
        return []
    with open(log_path, newline="") as f:
        r = csv.DictReader(f, delimiter="\t")
        rows = []
        for row in r:
            try:
                rows.append(
                    (int(row["ts"]), int(row["used_bytes"]), int(row["total_bytes"]))
                )
            except (KeyError, ValueError):
                continue
    rows.sort(key=lambda r: r[0])
    return rows


def slope_gb_per_day(log_path=LOG_PATH, days=30, now=None):
    """GB/day growth over the trailing `days`, from the oldest row at-or-after
    the window start to the newest row. None if fewer than two rows fall in
    the window (not enough history yet to trust a slope)."""
    rows = _read_rows(log_path)
    if not rows:
        return None
    cutoff = (now or time.time()) - days * 86400
    windowed = [r for r in rows if r[0] >= cutoff]
    if len(windowed) < 2:
        return None
    t0, u0, _ = windowed[0]
    t1, u1, _ = windowed[-1]
    span_days = (t1 - t0) / 86400.0
    if span_days <= 0:
        return None
    return (u1 - u0) / (1024**3) / span_days


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("record")
    sp = sub.add_parser("slope")
    sp.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)

    if args.cmd == "record":
        return record()
    if args.cmd == "slope":
        gbpd = slope_gb_per_day(days=args.days)
        if gbpd is None:
            print(f"slope: not enough history in the last {args.days}d yet")
            return 0
        snap = snapshot()
        avail_gb = None
        if snap:
            used, total, _ = snap
            avail_gb = (total - used) / (1024**3)
        msg = f"slope: {gbpd:+.3f} GB/day over the trailing {args.days}d"
        if avail_gb is not None and gbpd > 0:
            msg += f" ({avail_gb:.1f}G free -> ~{avail_gb / gbpd:.0f}d of runway at this rate)"
        print(msg)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
