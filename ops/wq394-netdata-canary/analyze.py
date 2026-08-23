#!/usr/bin/env python3
"""wq#394 canary gate — summarize measurements.csv and print a fleet-rollout recommendation.

Usage: analyze.py [--csv PATH] [--start-marker PATH] [--min-hours 48]

Thresholds are deliberately conservative placeholders (research doc cited an
unverified ~1% CPU claim for N100/Mac idle-tuned nodes): recommend GO only if
netdata's own CPU share stays under 2% average and package watts delta stays
under 3W average relative to the pre-install baseline noted in the report.
Frank makes the actual fleet-rollout call; this script produces the numbers,
not the decision.
"""
import argparse
import csv
import os
import statistics
import time

DEFAULT_CSV = "/var/lib/wq394-netdata-canary/measurements.csv"
DEFAULT_MARKER = "/var/lib/wq394-netdata-canary/soak_start"

CPU_PCT_GO_THRESHOLD = 2.0
WATTS_GO_THRESHOLD = 3.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--start-marker", default=DEFAULT_MARKER)
    ap.add_argument("--min-hours", type=float, default=48.0)
    ap.add_argument(
        "--ncpu", type=int, default=None,
        help="logical core count of the SAMPLED host (default: this host's). "
             "Set this when analyzing a CSV copied from another machine — the "
             "netdata CPU normalization below is wrong otherwise.",
    )
    args = ap.parse_args()

    if not os.path.exists(args.start_marker):
        print(f"NO_MARKER: {args.start_marker} not found — soak was never started here")
        return 2
    with open(args.start_marker) as f:
        start_ts = float(f.read().strip())
    elapsed_h = (time.time() - start_ts) / 3600
    print(f"soak started: {time.ctime(start_ts)} ({elapsed_h:.1f}h ago)")
    if elapsed_h < args.min_hours:
        print(f"TOO_EARLY: need {args.min_hours}h, only {elapsed_h:.1f}h elapsed")
        return 2

    if not os.path.exists(args.csv):
        print(f"NO_DATA: {args.csv} not found")
        return 2

    watts, cpu_pcts, rss = [], [], []
    with open(args.csv) as f:
        for row in csv.DictReader(f):
            if row["package_watts"]:
                watts.append(float(row["package_watts"]))
            if row["netdata_cpu_pct"]:
                cpu_pcts.append(float(row["netdata_cpu_pct"]))
            if row["netdata_rss_kb"]:
                rss.append((int(row["ts"]), int(row["netdata_rss_kb"])))

    if not watts or not cpu_pcts:
        print("INSUFFICIENT_SAMPLES: fewer than 2 rows recorded")
        return 2

    avg_watts = statistics.mean(watts)
    # sampler.py's netdata_cpu_pct is CPUUsageNSec-delta / wall-time — a
    # single-core-equivalent percentage (like `top`'s %CPU column, which can
    # run 0-100*ncpu), NOT already a share of total system capacity. The
    # go/hold thresholds below are expressed as system-wide share, so it must
    # be normalized by core count before comparing.
    ncpu = args.ncpu or os.cpu_count() or 1
    avg_cpu_per_core = statistics.mean(cpu_pcts)
    avg_cpu = avg_cpu_per_core / ncpu
    avg_rss_mb = statistics.mean(v for _, v in rss) / 1024 if rss else 0.0
    est_netdata_watts = avg_watts * (avg_cpu / 100)

    print(f"samples: {len(watts)}")
    print(f"avg package watts (whole host, RAPL pkg-0): {avg_watts:.2f}W")
    print(f"avg netdata CPU (single-core-equivalent, like top's %CPU): {avg_cpu_per_core:.3f}%")
    print(f"avg netdata CPU share of total ({ncpu} cores): {avg_cpu:.3f}%")
    print(f"avg netdata RSS: {avg_rss_mb:.1f}MB")
    if len(rss) >= 8:
        # RSS is not a gate criterion (no threshold is defined for it), but a
        # still-climbing working set at the end of the soak means the average
        # above is a snapshot of a moving number, so show the trend. wq#865
        # measured +8.6MB/h still rising at hour 24 on the first canary.
        tail = rss[-max(4, len(rss) // 4):]
        d_h = (tail[-1][0] - tail[0][0]) / 3600
        slope = (tail[-1][1] - tail[0][1]) / 1024 / d_h if d_h > 0 else 0.0
        print(f"netdata RSS trend: {rss[0][1] / 1024:.0f}MB -> {rss[-1][1] / 1024:.0f}MB "
              f"over the window, {slope:+.1f}MB/h across the final {d_h:.1f}h "
              f"(not gated — flag for review if still climbing at soak end)")
    print(f"estimated netdata power draw (pkg_watts * cpu_share): {est_netdata_watts:.3f}W")
    print(
        "NOTE: this is a proportional estimate, not a per-process power meter — "
        "cross-check against the pre-install baseline in reports/wq394_netdata_canary.md "
        "before treating it as ground truth."
    )

    go = avg_cpu < CPU_PCT_GO_THRESHOLD and est_netdata_watts < WATTS_GO_THRESHOLD
    print(f"RECOMMENDATION: {'GO' if go else 'HOLD'} "
          f"(thresholds: cpu<{CPU_PCT_GO_THRESHOLD}%, watts<{WATTS_GO_THRESHOLD}W)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
