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
    with open(PROC_STAT) as f:
        first = f.readline()
    parts = [int(x) for x in first.split()[1:]]
    return sum(parts)


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
    ts, energy, jiffies, cpu_ns = line.split(",")
    return float(ts), int(energy), int(jiffies), int(cpu_ns)


def save_state(ts, energy, jiffies, cpu_ns):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        f.write(f"{ts},{energy},{jiffies},{cpu_ns}\n")


def main():
    ts = time.time()
    energy = read_int(RAPL_ENERGY)
    max_range = read_int(RAPL_MAX_RANGE)
    jiffies = read_total_cpu_jiffies()
    cpu_ns = read_netdata_cpu_ns()
    rss_kb = read_netdata_rss_kb()

    prev = load_prev()
    save_state(ts, energy, jiffies, cpu_ns)

    row = {"ts": int(ts), "package_energy_uj": energy, "package_watts": "",
           "total_cpu_jiffies": jiffies, "total_cpu_pct": "",
           "netdata_cpu_ns": cpu_ns, "netdata_cpu_pct": "", "netdata_rss_kb": rss_kb}

    if prev is not None:
        prev_ts, prev_energy, prev_jiffies, prev_cpu_ns = prev
        dt = ts - prev_ts
        if dt > 0:
            d_energy = energy - prev_energy
            if d_energy < 0:
                d_energy += max_range  # counter wrapped
            row["package_watts"] = round((d_energy / 1_000_000) / dt, 3)

            clk_tck = os.sysconf("SC_CLK_TCK")
            d_jiffies = jiffies - prev_jiffies
            ncpu = os.cpu_count() or 1
            # total_cpu_jiffies sums all fields (incl. idle) across all cores,
            # so its max possible rate per wall-second is ncpu * clk_tck.
            row["total_cpu_pct"] = round(100 * d_jiffies / (dt * clk_tck * ncpu), 3)

            d_cpu_ns = cpu_ns - prev_cpu_ns
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
