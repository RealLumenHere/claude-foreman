#!/usr/bin/env python3
"""wq#394 canary sampler — RAPL package watts + netdata cgroup CPU%, appended to CSV.

Runs as root (needs /sys/class/powercap/intel-rapl read access). Invoked on a
timer (see wq394-netdata-sampler.timer) every 5 minutes for the 48h measurement
gate. Each row is a point sample plus the delta-derived rate since the previous
row, so the CSV is analyzable even if the timer misses a beat or the host reboots.
"""
import csv
import os
import subprocess
import sys
import time

RAPL_ENERGY = "/sys/class/powercap/intel-rapl:0/energy_uj"
RAPL_MAX_RANGE = "/sys/class/powercap/intel-rapl:0/max_energy_range_uj"
PROC_STAT = "/proc/stat"
CSV_PATH = "/var/lib/wq394-netdata-canary/measurements.csv"
STATE_PATH = "/var/lib/wq394-netdata-canary/.sampler_state"
FIELDS = [
    "ts", "package_energy_uj", "package_watts",
    "total_cpu_jiffies", "total_cpu_pct",
    "netdata_cpu_ns", "netdata_cpu_pct", "netdata_rss_kb",
]


def read_int(path):
    with open(path) as f:
        return int(f.read().strip())


def read_total_cpu_jiffies():
    """Return (all_fields_sum, busy, idle_all) from /proc/stat's aggregate cpu line.

    Fields are user nice system idle iowait irq softirq steal guest guest_nice.
    guest/guest_nice are already counted inside user/nice, so they are excluded
    from `busy` to avoid double-counting. `busy + idle_all` is the real
    denominator for a utilization ratio; the all-fields sum is kept only as the
    raw monotonic counter logged in the CSV.
    """
    with open(PROC_STAT) as f:
        first = f.readline()
    p = [int(x) for x in first.split()[1:]]
    p += [0] * (10 - len(p))
    user, nice, system, idle, iowait, irq, softirq, steal = p[:8]
    busy = user + nice + system + irq + softirq + steal
    return sum(p), busy, idle + iowait


def read_netdata_cpu_ns():
    out = subprocess.run(
        ["systemctl", "show", "netdata", "-p", "CPUUsageNSec", "--value"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(out) if out.isdigit() else 0


def read_netdata_rss_kb():
    out = subprocess.run(
        ["systemctl", "show", "netdata", "-p", "MemoryCurrent", "--value"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return int(out) // 1024 if out.isdigit() else 0


def load_prev():
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH) as f:
        line = f.read().strip()
    if not line:
        return None
    f = line.split(",")
    if len(f) == 4:
        # state written by the pre-wq#865 sampler: no busy/idle breakdown, so
        # the next row's total_cpu_pct is left blank rather than guessed.
        return float(f[0]), int(f[1]), int(f[3]), None, None
    ts, energy, _jiffies, cpu_ns, busy, idle_all = f
    return float(ts), int(energy), int(cpu_ns), int(busy), int(idle_all)


def save_state(ts, energy, jiffies, cpu_ns, busy, idle_all):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        f.write(f"{ts},{energy},{jiffies},{cpu_ns},{busy},{idle_all}\n")


def main():
    ts = time.time()
    energy = read_int(RAPL_ENERGY)
    max_range = read_int(RAPL_MAX_RANGE)
    jiffies, busy, idle_all = read_total_cpu_jiffies()
    cpu_ns = read_netdata_cpu_ns()
    rss_kb = read_netdata_rss_kb()

    prev = load_prev()
    save_state(ts, energy, jiffies, cpu_ns, busy, idle_all)

    row = {"ts": int(ts), "package_energy_uj": energy, "package_watts": "",
           "total_cpu_jiffies": jiffies, "total_cpu_pct": "",
           "netdata_cpu_ns": cpu_ns, "netdata_cpu_pct": "", "netdata_rss_kb": rss_kb}

    if prev is not None:
        prev_ts, prev_energy, prev_cpu_ns, prev_busy, prev_idle_all = prev
        dt = ts - prev_ts
        if dt > 0:
            d_energy = energy - prev_energy
            if d_energy < 0:
                d_energy += max_range  # counter wrapped
            row["package_watts"] = round((d_energy / 1_000_000) / dt, 3)

            if prev_busy is not None:
                # Host busy share: busy jiffies over busy+idle jiffies. Taking
                # the ratio makes this independent of clk_tck, core count and
                # wall-clock drift. The pre-wq#865 formula divided the sum of
                # ALL fields (idle included) by ncpu*clk_tck*dt, which is ~100%
                # by construction since every core is in exactly one state per
                # tick — it measured accounting coverage, not load.
                d_busy = busy - prev_busy
                d_all = d_busy + (idle_all - prev_idle_all)
                if d_all > 0:
                    row["total_cpu_pct"] = round(100 * d_busy / d_all, 3)

            d_cpu_ns = cpu_ns - prev_cpu_ns
            # Single-core-equivalent %, like top's %CPU column: ranges 0..100*ncpu.
            # NOT a share of total host capacity — analyze.py divides by ncpu.
            row["netdata_cpu_pct"] = round(100 * (d_cpu_ns / 1e9) / dt, 3)

    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    write_header = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerow(row)


if __name__ == "__main__":
    sys.exit(main())
