#!/usr/bin/env python3
"""Thermal backpressure (wq#2484): hold a hot node's subsystem, release it once the node cools.

The guard layer alerts on a hot node, but nothing slowed the work making the heat. This adds
no new control: it takes an slock on the node's subsystem, which the dispatcher already
refuses to spawn into (dispatcher/gates.py SubsystemHeld, b8c4609), the same way Slurm drains
a node that fails its health check. Work already running finishes; nothing new starts.

Triggers, per node, both read from netdata that is already there:
  temp       CPU package mean over sustain_min: hot above raise_c, cool below clear_c,
             unchanged in between (hysteresis). Thresholds: ops/thermal_backpressure.yaml.
  fan-floor  wq#2510's MacPro verdict (ops/fan_floor_alarm.py). This guard keeps its state.
A missing reading is no verdict and carries the state. It never reads as cool.

Exactly once, as bin/aq_escalate.py:13-21 does it: a hold or release is a row in the
`transitions` table, written in the same transaction that flips the node's state, so a poll
can create at most one. Side effects run step-wise off that row (applied_at, aq_at) and a
failure retries on the next poll. The aq key carries the transition id, so a retry after an
aq write whose bookkeeping failed bumps the same aq row and never opens a second.

  thermal_backpressure.py poll [--dry-run]        one cycle (lumen-thermal-backpressure.timer)
  thermal_backpressure.py status                  recorded state and recent transitions
  thermal_backpressure.py observe <node> [--days N]
                                                  observed range and the thresholds it implies
  thermal_backpressure.py replay --node N --after T --before T [--save CSV]
  thermal_backpressure.py replay --node N --csv CSV
                                                  temp-rule transitions over a window
"""
import argparse
import csv
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fan_floor_alarm  # noqa: E402
from netdata_window import fetch  # noqa: E402

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thermal_backpressure.yaml")
DB = os.environ.get("THERMAL_BP_DB", "/home/lumen/agents/thermal-backpressure/state.db")
SLOCK = os.environ.get("THERMAL_BP_SLOCK", "/home/lumen/bin/slock")
AQ = os.environ.get("THERMAL_BP_AQ", "/home/lumen/bin/aq")
AQ_SOURCE = "thermal-backpressure"
STEP_S = 60
THOR_NAME, THOR_IP = fan_floor_alarm.THOR_NAME, fan_floor_alarm.THOR_IP

SCHEMA = """
CREATE TABLE IF NOT EXISTS node_state(
  node TEXT PRIMARY KEY,
  temp_hot INTEGER NOT NULL DEFAULT 0,
  fan_hot INTEGER NOT NULL DEFAULT 0,
  held INTEGER NOT NULL DEFAULT 0,
  reading REAL, reason TEXT, polled_at TEXT);
CREATE TABLE IF NOT EXISTS transitions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node TEXT NOT NULL,
  kind TEXT NOT NULL,
  cause TEXT,
  created_at TEXT NOT NULL,
  applied_at TEXT, apply_note TEXT,
  aq_at TEXT);
"""


class ConfigError(ValueError):
    pass


def validate(cfg):
    for key in ("sustain_min", "hold_ttl_min"):
        if not isinstance(cfg.get(key), (int, float)) or cfg[key] <= 0:
            raise ConfigError(f"{key} missing or not a positive number")
    if not cfg.get("holder"):
        raise ConfigError("holder missing")
    sensor = cfg.get("sensor") or {}
    if not (sensor.get("chart_prefix") and sensor.get("chart_suffix")):
        raise ConfigError("sensor.chart_prefix and sensor.chart_suffix are required")
    nodes = cfg.get("nodes") or {}
    if (nodes.get(THOR_NAME) or {}).get("action") != "notify":
        raise ConfigError("thor must be listed with action: notify (instinct 2)")
    for name, node in nodes.items():
        action = node.get("action")
        if action not in ("hold", "notify"):
            raise ConfigError(f"{name}: action must be hold or notify, got {action!r}")
        host = str(node.get("netdata") or "").split(":")[0]
        if name == THOR_NAME or host == THOR_IP:
            if action != "notify" or node.get("hold_subsystems"):
                raise ConfigError(f"{name}: Thor may only notify; no hold, no hold_subsystems")
        if node.get("netdata"):
            r, c = node.get("raise_c"), node.get("clear_c")
            if not isinstance(r, (int, float)) or not isinstance(c, (int, float)):
                raise ConfigError(f"{name}: raise_c and clear_c are required with a netdata source")
            if c >= r:
                raise ConfigError(f"{name}: clear_c {c} must be below raise_c {r} (hysteresis)")
        if action == "hold":
            node.setdefault("hold_subsystems", [name])
            if not node["hold_subsystems"]:
                raise ConfigError(f"{name}: action hold needs at least one hold_subsystem")
    return cfg


def load_config(path=CONFIG):
    with open(path) as fh:
        return validate(yaml.safe_load(fh))


# ── decision ──────────────────────────────────────────────────────────────────────────────

def window_mean(win, n):
    """Mean of one sustain window of (ts, value) samples at STEP_S spacing, or None when the
    window is not fully covered (node down, booting, a gap)."""
    if len(win) != n or win[-1][0] - win[0][0] != (n - 1) * STEP_S:
        return None
    if any(v is None for _, v in win):
        return None
    return sum(v for _, v in win) / n


def next_temp(hot, mean, node):
    if mean is None:
        return hot
    return mean >= node["clear_c"] if hot else mean > node["raise_c"]


def next_fan(hot, verdict):
    """fan_floor_alarm.check() hands out both edges and leaves the state to its consumer."""
    if not verdict or verdict.get("raise") is None:
        return hot
    return not verdict["clear"] if hot else bool(verdict["raise"])


def evaluate(samples, node, sustain_min):
    """Temp-rule transitions over ascending (ts, value) samples at STEP_S spacing."""
    n = int(sustain_min * 60 // STEP_S)
    hot, out = False, []
    for i in range(n - 1, len(samples)):
        mean = window_mean(samples[i - n + 1:i + 1], n)
        now = next_temp(hot, mean, node)
        if now != hot:
            hot = now
            out.append({"ts": samples[i][0], "state": "raise" if now else "clear",
                        "mean": round(mean, 1)})
    return out


# ── netdata ───────────────────────────────────────────────────────────────────────────────

def resolve_chart(base, sensor):
    raw = subprocess.run(["curl", "-s", "-m", "30", f"http://{base}/api/v1/charts"],
                         capture_output=True, text=True, timeout=60).stdout
    if not raw:
        raise RuntimeError(f"{base}: netdata did not answer")
    prefix, suffix = sensor["chart_prefix"], sensor["chart_suffix"]
    hits = [c for c in json.loads(raw)["charts"] if c.startswith(prefix) and c.endswith(suffix)]
    if len(hits) != 1:
        raise RuntimeError(f"{base}: expected one {prefix}*{suffix} chart, found {hits}")
    return hits[0]


def read_mean(base, cfg, now):
    n = int(cfg["sustain_min"] * 60 // STEP_S)
    chart = resolve_chart(base, cfg["sensor"])
    # the newest minute is still filling: read two extra, drop the tail
    before = now - now % STEP_S
    after = before - (n + 2) * STEP_S
    series = fetch(base, chart, after, before, n + 2)
    samples = sorted(series.items())
    return window_mean(samples[-n - 1:-1], n)


def read_temps(cfg, now):
    out = {}
    for name, node in cfg["nodes"].items():
        if not node.get("netdata"):
            out[name] = (None, "no netdata source")
            continue
        try:
            mean = read_mean(node["netdata"], cfg, now)
            out[name] = (mean, None if mean is not None else "window not fully covered")
        except Exception as exc:
            out[name] = (None, f"{type(exc).__name__}: {exc}"[:200])
    return out


def read_fans(cfg):
    if not cfg.get("fan_floor"):
        return {}
    try:
        return {v["node"]: v for v in fan_floor_alarm.check(fan_floor_alarm.load_config())}
    except Exception as exc:
        print(f"fan-floor: no verdicts ({type(exc).__name__}: {exc})"[:300], file=sys.stderr)
        return {}


# ── actuation ─────────────────────────────────────────────────────────────────────────────

class Actuator:
    """slock for the hold, aq for the record. Both are the house CLIs, so a hold carries the
    same fence and retired-session rules as any other lock."""

    def __init__(self, holder, ttl_min, slock=SLOCK, aq=AQ):
        self.holder, self.ttl_min, self.slock, self.aq_bin = holder, ttl_min, slock, aq

    def _run(self, argv):
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        return p.returncode, (p.stdout + p.stderr).strip().splitlines()[:1]

    def hold(self, sub, note):
        """Acquire, or refresh our own lease. DENIED means a worker holds the subsystem: the
        dispatcher already skips it, and the next poll takes the hold once it is released."""
        rc, out = self._run([self.slock, "acquire", sub, "--holder", self.holder,
                             "--ttl", f"{self.ttl_min}m", "--note", note])
        return rc == 0, " ".join(out)

    def release(self, sub):
        """Only our own lease. Not held, or held by someone else, means ours is already gone."""
        rc, out = self._run([self.slock, "release", sub, "--holder", self.holder])
        text = " ".join(out)
        return rc == 0 or "held by" in text, text

    def aq(self, key, title, detail):
        rc, out = self._run([self.aq_bin, "add", AQ_SOURCE, key, title, detail,
                             "--owner", "claude"])
        return rc == 0, " ".join(out)


# ── state ─────────────────────────────────────────────────────────────────────────────────

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path=DB):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # autocommit: decide() is the one multi-statement transaction and opens its own
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def describe(name, node, mean, temp_hot, fan):
    parts = []
    if mean is not None and "raise_c" in node:
        parts.append(f"package {mean:.1f}C (raise >{node['raise_c']}, clear <{node['clear_c']})")
    if temp_hot:
        parts.append("temp hot")
    if fan and fan.get("te0t_mean") is not None:
        parts.append(f"fan-floor Te0T {fan['te0t_mean']}C fan {fan['fan_max']}rpm")
    return "; ".join(parts) or "no reading"


def decide(con, cfg, temps, fans):
    """Step every node once. The state flip and its transition row commit together."""
    decisions = []
    con.execute("BEGIN IMMEDIATE")
    try:
        for name, node in cfg["nodes"].items():
            row = con.execute("SELECT * FROM node_state WHERE node=?", (name,)).fetchone()
            was_temp, was_fan, was_held = (bool(row[k]) if row else False
                                           for k in ("temp_hot", "fan_hot", "held"))
            mean, reason = temps.get(name, (None, "not read"))
            fan = fans.get(name)
            temp_hot = next_temp(was_temp, mean, node) if "raise_c" in node else False
            fan_hot = next_fan(was_fan, fan)
            held = temp_hot or fan_hot
            cause = describe(name, node, mean, temp_hot, fan if fan_hot or was_fan else None)
            con.execute("""INSERT INTO node_state(node,temp_hot,fan_hot,held,reading,reason,polled_at)
                           VALUES(?,?,?,?,?,?,?) ON CONFLICT(node) DO UPDATE SET
                           temp_hot=excluded.temp_hot, fan_hot=excluded.fan_hot, held=excluded.held,
                           reading=excluded.reading, reason=excluded.reason,
                           polled_at=excluded.polled_at""",
                        (name, int(temp_hot), int(fan_hot), int(held),
                         None if mean is None else round(mean, 2), reason, utcnow()))
            tid = None
            if held != was_held:
                kind = ("hold" if held else "release") if node["action"] == "hold" else \
                       ("hot" if held else "cool")
                tid = con.execute("INSERT INTO transitions(node,kind,cause,created_at) "
                                  "VALUES(?,?,?,?)", (name, kind, cause, utcnow())).lastrowid
            decisions.append({"node": name, "held": held, "temp_hot": temp_hot,
                              "fan_hot": fan_hot, "mean": mean, "reason": reason,
                              "transition": tid})
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return decisions


def actuate(con, cfg, act):
    """Hold every held node's subsystems (acquire or refresh), then settle pending
    transitions: release what cooled, and write each transition's single aq row."""
    notes = {}
    for name, node in cfg["nodes"].items():
        if node["action"] != "hold":
            continue
        st = con.execute("SELECT held FROM node_state WHERE node=?", (name,)).fetchone()
        if st and st["held"]:
            res = [(sub, *act.hold(sub, f"wq#2484 thermal hold: {name} running hot"))
                   for sub in node["hold_subsystems"]]
            notes[name] = res
    pending = con.execute("SELECT id FROM transitions WHERE applied_at IS NULL OR aq_at IS NULL "
                          "ORDER BY id").fetchall()
    for (tid,) in pending:
        tr = con.execute("SELECT * FROM transitions WHERE id=?", (tid,)).fetchone()
        node = cfg["nodes"].get(tr["node"])
        if node is None:
            continue
        if tr["applied_at"] is None:
            ok, note = apply_transition(con, tr, node, notes.get(tr["node"]), act)
            con.execute("UPDATE transitions SET applied_at=?, apply_note=? WHERE id=?",
                        (utcnow() if ok else None, note[:500], tid))
        if tr["aq_at"] is None:
            tr = con.execute("SELECT * FROM transitions WHERE id=?", (tid,)).fetchone()
            subs = ",".join(node.get("hold_subsystems") or [])
            title = {"hold": f"thermal hold: {tr['node']} hot, holding {subs}",
                     "release": f"thermal release: {tr['node']} cooled, released {subs}",
                     "hot": f"thermal: {tr['node']} hot (notify only)",
                     "cool": f"thermal: {tr['node']} cooled (notify only)"}[tr["kind"]]
            detail = f"{tr['cause']} | {tr['apply_note'] or 'apply pending'} | wq#2484 transition {tid}"
            ok, _ = act.aq(f"{tr['node']}:{tr['kind']}:{tid}", title, detail[:1500])
            if ok:
                con.execute("UPDATE transitions SET aq_at=? WHERE id=?", (utcnow(), tid))
    return notes


def apply_transition(con, tr, node, holds, act):
    """(done, note) for one pending transition. A hold is done once every subsystem is ours;
    until then the per-poll refresh keeps trying. A release frees only our own leases."""
    held = con.execute("SELECT held FROM node_state WHERE node=?", (tr["node"],)).fetchone()["held"]
    if tr["kind"] == "hold":
        if not held:
            return True, "superseded: node cooled before the hold took"
        return all(ok for _, ok, _ in holds), "; ".join(f"{s}: {m}" for s, _, m in holds)
    if tr["kind"] == "release":
        if held:
            return True, "superseded: node hot again"
        res = [(s, *act.release(s)) for s in node["hold_subsystems"]]
        return all(ok for _, ok, _ in res), "; ".join(f"{s}: {m}" for s, _, m in res)
    return True, "notify only"


def poll(con, cfg, act, temps, fans):
    decisions = decide(con, cfg, temps, fans)
    actuate(con, cfg, act)
    return decisions


# ── commands ──────────────────────────────────────────────────────────────────────────────

def observe(cfg, name, days):
    node = cfg["nodes"][name]
    base, now = node["netdata"], int(time.time())
    chart = resolve_chart(base, cfg["sensor"])
    step = 300
    series = fetch(base, chart, now - days * 86400, now, days * 86400 // step)
    rows = sorted((t, v) for t, v in series.items() if v is not None)
    if not rows:
        return {"node": name, "error": "no retained data"}
    vals = sorted(v for _, v in rows)
    q = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]  # noqa: E731
    peak = max(rows, key=lambda r: r[1])
    span_d = (rows[-1][0] - rows[0][0]) / 86400
    return {"node": name, "span_days": round(span_d, 1), "points": len(rows),
            "p50": round(q(.5), 1), "p99": round(q(.99), 1), "max": round(peak[1], 1),
            "max_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(peak[0])),
            "suggest_raise_c": math.ceil(peak[1]) + 3, "suggest_clear_c": math.floor(q(.99)),
            "enough_history": span_d >= 7}


def read_csv(path):
    with open(path) as fh:
        return [(int(r["ts"]), float(r["temp"]) if r["temp"] else None)
                for r in csv.DictReader(fh)]


def write_csv(path, samples):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "temp"])
        w.writerows((ts, "" if v is None else v) for ts, v in samples)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--db", default=DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll")
    p.add_argument("--dry-run", action="store_true", help="read and decide in memory; no state, lock or aq write")
    sub.add_parser("status")
    o = sub.add_parser("observe")
    o.add_argument("node")
    o.add_argument("--days", type=int, default=40)
    r = sub.add_parser("replay")
    r.add_argument("--node", required=True)
    r.add_argument("--csv", help="replay a saved window instead of querying netdata")
    r.add_argument("--after", type=int)
    r.add_argument("--before", type=int)
    r.add_argument("--save", help="write the fetched window to this CSV")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "observe":
        print(json.dumps(observe(cfg, a.node, a.days)))
        return 0
    if a.cmd == "replay":
        node = cfg["nodes"][a.node]
        if a.csv:
            samples = read_csv(a.csv)
        else:
            if not (a.after and a.before):
                ap.error("replay needs --after and --before, or --csv")
            after, before = a.after - a.after % STEP_S, a.before - a.before % STEP_S
            chart = resolve_chart(node["netdata"], cfg["sensor"])
            samples = sorted(fetch(node["netdata"], chart, after, before,
                                   (before - after) // STEP_S).items())
            if a.save:
                write_csv(a.save, samples)
        for t in evaluate(samples, node, cfg["sustain_min"]):
            print(f"{time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(t['ts']))} {t['state']:5} mean={t['mean']}")
        return 0
    if a.cmd == "status":
        con = connect(a.db)
        for row in con.execute("SELECT * FROM node_state ORDER BY node"):
            print(json.dumps(dict(row)))
        for row in con.execute("SELECT * FROM transitions ORDER BY id DESC LIMIT 10"):
            print(json.dumps(dict(row)))
        return 0

    temps, fans = read_temps(cfg, int(time.time())), read_fans(cfg)
    con = connect(":memory:" if a.dry_run else a.db)
    decisions = decide(con, cfg, temps, fans)
    if not a.dry_run:
        actuate(con, cfg, Actuator(cfg["holder"], cfg["hold_ttl_min"]))
    for d in decisions:
        mean = "-" if d["mean"] is None else f"{d['mean']:.1f}"
        print(f"{d['node']:9} held={d['held']} temp={mean} temp_hot={d['temp_hot']} "
              f"fan_hot={d['fan_hot']}" + (f" transition=#{d['transition']}" if d["transition"] else "")
              + (f" ({d['reason']})" if d["reason"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
