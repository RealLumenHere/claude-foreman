#!/usr/bin/env python3
"""reaper — independent wq lease reaper. RELIABILITY_SPEC.md §E, wq#647 (fq#614 fix).

wq's own lease-expiry reclaim is lazy: it only runs inside `wq claim`, against the ONE
lane being claimed, as a side effect of somebody else wanting work. A lane nobody
happens to claim from can sit on a dead holder's dangling claim indefinitely — fq#614
catalogued three same-day drain-deaths (a supervisor process itself dying mid-ticket,
not just its `claude` child) that dangled for hours until the orchestrator noticed and
retired them by hand. `wq reap` (harness's own addition, this build) sweeps every lane
on its own schedule, independent of any claim/heartbeat/done path; this script drives
it and turns every reclaim into an aq row, so a dangling claim is never silent again.

Usage:
    reaper.py sweep [--lane LANE]         one pass, prints what it reaped
    reaper.py loop [--interval SEC] [--lane LANE]   repeat forever (lumen-run/cron target)
"""
import argparse
import os
import re
import subprocess
import sys
import time

VERSION = "0.1.0"

WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
AQ_CLI = os.environ.get("FOREMAN_AQ_CLI", "/home/lumen/agents/alert-queue/aq.py")
DEFAULT_INTERVAL_S = 120

REAPED_RE = re.compile(
    r"^REAPED #(?P<id>\d+) lane=(?P<lane>\S+) holder=(?P<holder>\S+) "
    r"claimed_ago_min=(?P<claimed_ago>\S+) lease_expired_ago_min=(?P<lease_ago>\S+) "
    r"title=(?P<title>.+)$"
)


def log(msg):
    print(f"[reaper {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}", flush=True)


def run_wq_reap(lane=None):
    cmd = [WQ_BIN, "reap"]
    if lane:
        cmd += ["--lane", lane]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        log(f"wq reap failed rc={p.returncode}: {p.stdout.strip()} {p.stderr.strip()}")
        return []
    reaped = []
    for line in p.stdout.splitlines():
        m = REAPED_RE.match(line.strip())
        if m:
            reaped.append(m.groupdict())
    return reaped


def post_aq_row(reaped):
    """One aq row per reclaim (never silent, per the ticket) — dedup on (source, key)
    is aq's own job (ingest() bumps count on the same wq<id> key), so a ticket that
    keeps dying and getting reaped shows up as ONE bumped row, not a flood."""
    title = f"wq#{reaped['id']} lease reaped: {reaped['title'].strip()[:200]}"
    message = (f"holder={reaped['holder']} lane={reaped['lane']} "
               f"claimed_ago_min={reaped['claimed_ago']} "
               f"lease_expired_ago_min={reaped['lease_ago']}")
    cmd = [sys.executable, AQ_CLI, "add", "foreman-reaper", f"wq{reaped['id']}", title,
           message, "--owner", "orchestrator"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if p.returncode != 0:
        log(f"aq add failed for wq#{reaped['id']}: {p.stdout.strip()} {p.stderr.strip()}")
        return False
    return True


def sweep(lane=None):
    reaped = run_wq_reap(lane)
    for r in reaped:
        log(f"reclaimed wq#{r['id']} (lane={r['lane']}, was held by {r['holder']}) "
            f"— posting aq row")
        post_aq_row(r)
    if not reaped:
        log("nothing to reap")
    return reaped


def cmd_sweep(args):
    sweep(args.lane)
    return 0


def cmd_loop(args):
    log(f"loop starting: interval={args.interval}s lane={args.lane or 'all'}")
    while True:
        try:
            sweep(args.lane)
        except Exception as e:
            log(f"sweep failed (non-fatal, retrying next interval): {e}")
        time.sleep(args.interval)


def main():
    if "--version" in sys.argv[1:]:
        print(f"reaper.py {VERSION}")
        sys.exit(0)

    ap = argparse.ArgumentParser(description="reaper — independent wq lease sweep")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sweep = sub.add_parser("sweep", help="one pass over every lane (or --lane)")
    p_sweep.add_argument("--lane", default=None)
    p_sweep.set_defaults(func=cmd_sweep)

    p_loop = sub.add_parser("loop", help="repeat sweep forever — the lumen-run/cron target")
    p_loop.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    p_loop.add_argument("--lane", default=None)
    p_loop.set_defaults(func=cmd_loop)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
