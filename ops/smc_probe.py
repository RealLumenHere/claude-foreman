#!/usr/bin/env python3
"""Read applesmc temps + fan rpm from a fleet node through the fleet-exec broker.

wq#2140. The broker only allows read-only argv (cat/ls/...), no shell, so every
read is an explicit path list handed to one `cat`.

  smc_probe.py labels <node>            -- dump sensor index -> SMC key
  smc_probe.py sample <node> [keys...]  -- one reading of the named SMC keys + fan
  smc_probe.py watch <node> <secs> <interval> [keys...]
"""
import subprocess
import sys
import time

FLEET_EXEC = "/home/lumen/bin/fleet-exec"
SMC = "/sys/devices/platform/applesmc.768"
MAX_TEMP_IDX = 110
FAN_ATTRS = ("fan1_input", "fan1_min", "fan1_max", "fan1_manual", "fan1_output", "fan1_safe")


def read(node, paths):
    """cat the paths on `node`; return one list entry per path ('' when missing)."""
    out = subprocess.run(
        [FLEET_EXEC, "run", node, "--", "cat"] + list(paths),
        capture_output=True, text=True, timeout=120,
    )
    lines = out.stdout.splitlines()
    # cat emits one line per readable file, in argv order, and sends the
    # "No such file" complaints to stderr, so a missing path shifts the rest.
    if len(lines) == len(paths):
        return lines
    missing = set()
    for err in out.stderr.splitlines():
        for p in paths:
            if p in err:
                missing.add(p)
    res, it = [], iter(lines)
    for p in paths:
        res.append("" if p in missing else next(it, ""))
    return res


def labels(node):
    paths = [f"{SMC}/temp{i}_label" for i in range(1, MAX_TEMP_IDX + 1)]
    vals = read(node, paths)
    return {v.strip(): i for i, v in enumerate(vals, start=1) if v.strip()}


def sample(node, index, keys):
    paths = [f"{SMC}/temp{index[k]}_input" for k in keys if k in index]
    paths += [f"{SMC}/{a}" for a in FAN_ATTRS]
    vals = read(node, paths)
    temps = {}
    for k in [k for k in keys if k in index]:
        v = vals.pop(0).strip()
        temps[k] = round(int(v) / 1000.0, 1) if v.isdigit() else None
    fans = dict(zip(FAN_ATTRS, [v.strip() for v in vals]))
    return temps, fans


def fmt(node, temps, fans):
    t = " ".join(f"{k}={v}" for k, v in temps.items())
    return (
        f"{time.strftime('%H:%M:%SZ', time.gmtime())} {node} {t} "
        f"fan={fans.get('fan1_input')} min={fans.get('fan1_min')} "
        f"max={fans.get('fan1_max')} manual={fans.get('fan1_manual')}"
    )


def main():
    mode, node = sys.argv[1], sys.argv[2]
    if mode == "labels":
        index = labels(node)
        print(f"{node}: {len(index)} sensors")
        print(" ".join(f"{k}:{i}" for k, i in sorted(index.items())))
        return
    if mode == "sample":
        keys = sys.argv[3:]
        index = labels(node)
        print(fmt(node, *sample(node, index, keys)))
        return
    if mode == "watch":
        secs, interval, keys = int(sys.argv[3]), int(sys.argv[4]), sys.argv[5:]
        index = labels(node)
        deadline = time.time() + secs
        while time.time() < deadline:
            try:
                print(fmt(node, *sample(node, index, keys)), flush=True)
            except Exception as exc:  # node went away mid-watch: that is the datum
                print(f"{time.strftime('%H:%M:%SZ', time.gmtime())} {node} PROBE-FAIL {exc}", flush=True)
            time.sleep(interval)
        return
    sys.exit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
