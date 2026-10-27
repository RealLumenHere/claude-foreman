#!/usr/bin/env python3
"""MacPro6,1 fan-floor alarm (wq#2510): Te0T held above 80 C while fan1 stays under 1000 rpm.

research/ISIS_POWEROFF_RCA.md:84-105 found isis at 789-797 rpm with Te0T 81-84 C for two days,
and only because a human hand-queried netdata during an RCA. netdata already retains both
series; this reads them and decides. It never acts: wq#2484's thermal-backpressure guard
consumes `check --json` and owns the hold and its aq row. Rule and nodes live in
ops/fan_floor_alarm.yaml; load_config() enforces the Thor notify-only carve-out.

  fan_floor_alarm.py check [--json]                       live verdict per node, read-only
  fan_floor_alarm.py replay <node> <after> <before> [--save CSV]
                                                          transitions over a retained window
  fan_floor_alarm.py replay --csv CSV                     same, from a saved window
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netdata_window import fetch  # noqa: E402

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fan_floor_alarm.yaml")
STEP_S = 60
# Instinct 2. Hard-coded so that no config edit can relax it.
THOR_NAME, THOR_IP = "thor", "100.112.151.100"
# Chart ids carry a hwmon index that is not stable across kernels (ISIS_POWEROFF_RCA.md:242),
# so resolve by suffix, not by `temp98`.
TE0T_CHART = ("sensors.temperature_applesmc", "_Te0T_input")
FAN_CHART = ("sensors.fan_applesmc", "_fan1_Main_input")


class ConfigError(ValueError):
    pass


def validate(cfg):
    rule = cfg.get("rule") or {}
    for key in ("te0t_above_c", "te0t_clear_below_c", "fan_below_rpm", "sustain_min"):
        if not isinstance(rule.get(key), (int, float)):
            raise ConfigError(f"rule.{key} missing or not a number")
    if rule["te0t_clear_below_c"] > rule["te0t_above_c"]:
        raise ConfigError("rule.te0t_clear_below_c must not exceed te0t_above_c")
    nodes = cfg.get("nodes") or {}
    if (nodes.get(THOR_NAME) or {}).get("action") != "notify":
        raise ConfigError("thor must be listed with action: notify (instinct 2)")
    for name, node in nodes.items():
        action = node.get("action")
        if action not in ("hold", "notify"):
            raise ConfigError(f"{name}: action must be hold or notify, got {action!r}")
        host = str(node.get("netdata") or "").split(":")[0]
        if name == THOR_NAME or host == THOR_IP:
            if action != "notify" or node.get("hold_subsystem"):
                raise ConfigError(f"{name}: Thor may only notify; no action, no hold_subsystem")
        elif action == "hold" and not node.get("hold_subsystem"):
            raise ConfigError(f"{name}: action hold needs hold_subsystem")
    return cfg


def load_config(path=CONFIG):
    with open(path) as fh:
        return validate(yaml.safe_load(fh))


def window_stats(win, rule):
    """(te0t_mean, fan_max) over one sustain window of (ts, te0t, fan) samples — the RCA's
    group=average bucket for Te0T, the max for "fan stays under" — or None when the window
    is not fully covered (node down, booting, or a gap)."""
    n = int(rule["sustain_min"] * 60 // STEP_S)
    if len(win) != n or win[-1][0] - win[0][0] != (n - 1) * STEP_S:
        return None
    if any(t is None or f is None for _, t, f in win):
        return None
    return sum(t for _, t, _ in win) / n, max(f for _, _, f in win)


def next_state(firing, stats, rule):
    """The alarm's state after one window. No stats means no verdict: the state carries."""
    if stats is None:
        return firing
    te0t, fan = stats
    if fan >= rule["fan_below_rpm"]:
        return False
    return te0t >= rule["te0t_clear_below_c"] if firing else te0t > rule["te0t_above_c"]


def evaluate(samples, rule):
    """Raise/clear transitions over ascending (ts, te0t, fan) samples at STEP_S spacing."""
    n = int(rule["sustain_min"] * 60 // STEP_S)
    firing, out = False, []
    for i in range(n - 1, len(samples)):
        stats = window_stats(samples[i - n + 1:i + 1], rule)
        now = next_state(firing, stats, rule)
        if now != firing:
            firing = now
            out.append({"ts": samples[i][0], "state": "raise" if now else "clear",
                        "te0t_mean": round(stats[0], 1), "fan_max": stats[1]})
    return out


def resolve_charts(base):
    raw = subprocess.run(["curl", "-s", "-m", "30", f"http://{base}/api/v1/charts"],
                         capture_output=True, text=True, timeout=60).stdout
    if not raw:
        raise RuntimeError(f"{base}: netdata did not answer")
    charts = json.loads(raw)["charts"]
    found = []
    for prefix, suffix in (TE0T_CHART, FAN_CHART):
        hits = [c for c in charts if c.startswith(prefix) and c.endswith(suffix)]
        if len(hits) != 1:
            raise RuntimeError(f"{base}: expected one {prefix}*{suffix} chart, found {hits}")
        found.append(hits[0])
    return found


def fetch_samples(base, after, before):
    te0t_chart, fan_chart = resolve_charts(base)
    # both ends on the minute, or netdata picks a 61 s step and no window ever covers
    after, before = after - after % STEP_S, before - before % STEP_S
    points = (before - after) // STEP_S
    te0t = fetch(base, te0t_chart, after, before, points)
    fan = fetch(base, fan_chart, after, before, points)
    return [(ts, te0t.get(ts), fan.get(ts)) for ts in sorted(set(te0t) | set(fan))]


def read_csv(path):
    num = lambda v: float(v) if v else None  # noqa: E731
    with open(path) as fh:
        return [(int(r["ts"]), num(r["te0t"]), num(r["fan"])) for r in csv.DictReader(fh)]


def write_csv(path, samples):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "te0t", "fan"])
        w.writerows(["" if v is None else v for v in row] for row in samples)


def stamp(ts):
    return time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(ts))


def check(cfg):
    """One verdict per node for the latest complete window. `raise` and `clear` are the two
    edges of next_state(); the consumer keeps the state and calls next_state() itself.
    Both are None when there is no verdict, which must never read as a clear."""
    rule, now = cfg["rule"], int(time.time())
    n = int(rule["sustain_min"] * 60 // STEP_S)
    out = []
    for name, node in cfg["nodes"].items():
        v = {"node": name, "action": node["action"], "hold_subsystem": node.get("hold_subsystem"),
             "raise": None, "clear": None, "te0t_mean": None, "fan_max": None, "reason": None}
        stats = None
        if node.get("retired"):
            # wq#4714: the hardware is gone. Fetching from it costs a 30 s curl per call and
            # cannot produce a verdict, so skip the host rather than time out against it.
            v["reason"] = "node retired"
        elif not node.get("netdata"):
            v["reason"] = "no netdata source"
        else:
            try:
                # the newest minute is still filling, so read two extra and drop the tail
                samples = fetch_samples(node["netdata"], now - (n + 2) * STEP_S, now)
                stats = window_stats(samples[-n - 1:-1], rule)
                v["reason"] = None if stats else "window not fully covered"
            except Exception as exc:
                v["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        if stats:
            v["raise"], v["clear"] = next_state(False, stats, rule), not next_state(True, stats, rule)
            v["te0t_mean"], v["fan_max"] = round(stats[0], 1), stats[1]
        out.append(v)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    r = sub.add_parser("replay")
    r.add_argument("node", nargs="?")
    r.add_argument("after", nargs="?", type=int)
    r.add_argument("before", nargs="?", type=int)
    r.add_argument("--csv", help="replay a saved window instead of querying netdata")
    r.add_argument("--save", help="write the fetched window to this CSV")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "check":
        verdicts = check(cfg)
        for v in verdicts:
            print(json.dumps(v) if a.json else
                  f"{v['node']:9} raise={v['raise']} clear={v['clear']} "
                  f"te0t={v['te0t_mean']} fan={v['fan_max']} "
                  f"action={v['action']}{' ' + v['reason'] if v['reason'] else ''}")
        return 0

    if a.csv:
        samples = read_csv(a.csv)
    else:
        if not (a.node and a.after and a.before):
            ap.error("replay needs <node> <after> <before>, or --csv")
        base = cfg["nodes"][a.node]["netdata"]
        # lead-in so the first evaluated window already ends at <after>
        samples = fetch_samples(base, a.after - int(cfg["rule"]["sustain_min"] * 60), a.before)
        if a.save:
            write_csv(a.save, samples)
    for t in evaluate(samples, cfg["rule"]):
        print(f"{stamp(t['ts'])} {t['state']:5} te0t_mean={t['te0t_mean']} fan_max={t['fan_max']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
