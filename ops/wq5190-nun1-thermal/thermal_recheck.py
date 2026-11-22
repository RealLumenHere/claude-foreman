#!/usr/bin/env python3
"""wq#5190: nun1 (BC-250) clean thermal recheck, replacing scratch/bc250-soak/run_thermal_recheck.sh.

The wq#5126 waiter ran 8h (2026-11-20 07:04-15:04 PST) and gave up "never idle 10 min"
without recording a single reason, so why nun1 never looked idle is unknowable after the
fact. Its idle test had two defects this runner fixes:

1. Busy == "Ollama /api/ps lists any model". A *resident* model is not a *running*
   request; with a long keep_alive the list is never empty, so an idle blade reads busy
   forever. Here the primary signal is GPU load (amdgpu gpu_busy_percent); the model
   list is only the fallback when that sysfs file is unreadable.
2. An ssh/curl failure produced empty output, which the shell script counted as IDLE,
   so an unreachable nun1 could have "passed" the idle wait. Here unreachable is its
   own state and resets the idle streak.

Every sample is appended to <log>.trace (one line per minute) and the give-up line carries
the per-reason tally, so a failed wait now says why.

Usage (on Lumen, detached; nun1 must be up -- see wq#5225):
  systemd-run --user --unit wq5190-thermal-recheck \
    python3 /home/lumen/foreman/ops/wq5190-nun1-thermal/thermal_recheck.py
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

SOAK_DIR = "/home/lumen/scratch/bc250-soak"
DEFAULT_LOG = f"{SOAK_DIR}/validate_qwen3_14b.log"
DEFAULT_VALIDATOR = f"{SOAK_DIR}/validate_qwen3_14b.py"
DEFAULT_LOCK = f"{SOAK_DIR}/nun1-bench.lock"
GPU_IDLE_MAX = 10  # percent; idle desktop amdgpu reads 0-3
LOCAL_BENCH_RE = r"^python3 (-I )?[^ ]*(judge_bench|soak|validate_qwen3_14b)\.py"
SEP = "@@SECTION@@"
REMOTE_PROBE = (
    "cat /sys/class/drm/card*/device/gpu_busy_percent 2>/dev/null; echo " + SEP + "; "
    "sensors 2>/dev/null | grep -E '^(Tctl|edge):'; echo " + SEP + "; "
    "curl -s -m 5 localhost:11434/api/ps"
)


def parse_probe(text):
    """Parse REMOTE_PROBE output -> dict(gpu_busy, tctl, models). None = unknown."""
    parts = text.split(SEP)
    if len(parts) != 3:
        return None
    busy = [int(x) for x in re.findall(r"^\s*(\d+)\s*$", parts[0], re.M)]
    m = re.search(r"Tctl:\s*\+?([\d.]+)", parts[1])
    try:
        models = [x.get("name", "?") for x in json.loads(parts[2])["models"] or []]
    except (ValueError, KeyError, TypeError):
        models = None
    return {
        "gpu_busy": max(busy) if busy else None,
        "tctl": float(m[1]) if m else None,
        "models": models,
    }


def classify(local_bench, probe):
    """Return (idle: bool, reason: str) for one sample."""
    if local_bench:
        return False, "local_bench_process"
    if probe is None:
        return False, "nun1_unreachable"
    if probe["gpu_busy"] is not None:
        if probe["gpu_busy"] > GPU_IDLE_MAX:
            return False, "gpu_busy"
        return True, "idle_gpu"
    # Fallback: no GPU load counter -> the old (conservative) loaded-model test.
    if probe["models"] is None:
        return False, "ollama_unreadable"
    if probe["models"]:
        return False, "models_resident_no_gpu_counter"
    return True, "idle_no_models"


def pst():
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%F %T %Z")


def append(path, line):
    with open(path, "a") as f:
        f.write(f"[{pst()}] {line}\n")


def sample(ssh_host):
    local = subprocess.run(["pgrep", "-f", LOCAL_BENCH_RE], capture_output=True).returncode == 0
    try:
        o = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", ssh_host,
                           REMOTE_PROBE], capture_output=True, text=True, timeout=30)
        probe = parse_probe(o.stdout) if o.returncode == 0 else None
    except subprocess.TimeoutExpired:
        probe = None
    return local, probe


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--validator", default=DEFAULT_VALIDATOR)
    ap.add_argument("--lock", default=DEFAULT_LOCK)
    ap.add_argument("--host", default="nun1")
    ap.add_argument("--idle-minutes", type=int, default=10)
    ap.add_argument("--deadline-hours", type=float, default=8)
    ap.add_argument("--interval", type=int, default=60)
    a = ap.parse_args(argv)
    trace = a.log + ".trace"
    deadline = time.time() + a.deadline_hours * 3600
    streak, tally = 0, Counter()
    append(a.log, f"THERMAL WAIT (wq#5190 runner) started: idle={a.idle_minutes}min "
                  f"gpu_busy<={GPU_IDLE_MAX}% deadline={a.deadline_hours}h")
    while streak < a.idle_minutes:
        if time.time() > deadline:
            append(a.log, f"THERMAL RECHECK GAVE UP: nun1 never idle {a.idle_minutes} min within "
                          f"{a.deadline_hours}h; samples {dict(tally)}")
            return 3
        local, probe = sample(a.host)
        idle, reason = classify(local, probe)
        tally[reason] += 1
        streak = streak + 1 if idle else 0
        append(trace, f"{reason} streak={streak} probe={json.dumps(probe)}")
        if streak < a.idle_minutes:
            time.sleep(a.interval)
    append(a.log, f"THERMAL RECHECK: nun1 idle {a.idle_minutes} min, starting "
                  f"(baseline {json.dumps(probe)})")
    os.execvp("flock", ["flock", "-w", "3600", a.lock, "python3", "-I", a.validator])


if __name__ == "__main__":
    sys.exit(main())
