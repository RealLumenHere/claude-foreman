#!/usr/bin/env python3
"""disk_pct_alarm.py — per-mount disk-usage-percent alarm (wq#3171).

fleet-audit-2 (wq#3162) found /data/lumen (md0 RAID5, 3/3 healthy) at
428G/469G (97%) with no aq row for it: `lumen-disk-hygiene` (bin/lumen-disk-hygiene)
already alarms root at 85%, but nothing watches non-root mounts, so /data/lumen
could fill silently past 97% with zero page. This is a generic per-mount
watcher, not a one-off for /data/lumen, so the next mount that fills (/scratch,
/mnt/lumen-backup) is one config entry away rather than a new script.

EDGE-triggered like aer_storm_alarm (wq#2851) and phoenix_disk_guard (wq#1299):
one aq row + one ntfy on raise, resolved with one RECOVERED ntfy on clear,
nothing repeats while a mount stays in the same state. Hysteresis
(raise_at_or_above / clear_below) avoids flapping right at the line.
Thresholds live in disk_pct_alarm.yaml, not here, so a mount's limit can be
tuned without a code change.

  disk_pct_alarm.py check [--json] [--dry-run] [--config PATH]
"""
import argparse
import json
import os
import shutil
import sys

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "disk_pct_alarm.yaml")
SOURCE = "disk-pct"
AQ_DIR = os.environ.get("DISK_PCT_AQ_DIR", "/home/lumen/agents/alert-queue")
STATE_DIR = os.environ.get("DISK_PCT_STATE_DIR", "/home/lumen/state/disk-pct-alarm")
NTFY_BIN = os.environ.get("DISK_PCT_NTFY_BIN", "/home/lumen/bin/ntfy-send")

DEFAULT_RAISE = 90
DEFAULT_CLEAR = 85


class ConfigError(ValueError):
    pass


def default_config():
    return {
        "defaults": {"raise_at_or_above": DEFAULT_RAISE, "clear_below": DEFAULT_CLEAR},
        "mounts": {"data-lumen": {"path": "/data/lumen"}},
    }


def validate(cfg):
    defaults = cfg.get("defaults") or {}
    raise_at = defaults.get("raise_at_or_above", DEFAULT_RAISE)
    clear_at = defaults.get("clear_below", DEFAULT_CLEAR)
    if not isinstance(raise_at, (int, float)) or not isinstance(clear_at, (int, float)):
        raise ConfigError("defaults.raise_at_or_above / clear_below must be numbers")
    if clear_at > raise_at:
        raise ConfigError("defaults.clear_below must not exceed defaults.raise_at_or_above")
    mounts = cfg.get("mounts") or {}
    if not mounts:
        raise ConfigError("mounts must not be empty")
    for name, m in mounts.items():
        if not isinstance(m, dict) or not m.get("path"):
            raise ConfigError(f"mounts.{name}.path missing")
        r = m.get("raise_at_or_above", raise_at)
        c = m.get("clear_below", clear_at)
        if not isinstance(r, (int, float)) or not isinstance(c, (int, float)):
            raise ConfigError(f"mounts.{name} thresholds must be numbers")
        if c > r:
            raise ConfigError(f"mounts.{name}.clear_below must not exceed raise_at_or_above")
    return cfg


def load_config(path=CONFIG):
    if not os.path.exists(path):
        return default_config()
    import yaml
    with open(path) as fh:
        return validate(yaml.safe_load(fh))


def mount_rule(cfg, name, m):
    defaults = cfg.get("defaults") or {}
    return {
        "raise_at_or_above": m.get("raise_at_or_above", defaults.get("raise_at_or_above", DEFAULT_RAISE)),
        "clear_below": m.get("clear_below", defaults.get("clear_below", DEFAULT_CLEAR)),
    }


# --------------------------------------------------------------- plumbing ---
def aq_mod():
    sys.path.insert(0, AQ_DIR)
    import aq
    return aq


def ntfy(title, msg, prio):
    try:
        import subprocess
        subprocess.run([NTFY_BIN, "-p", prio, "-T", title, msg],
                        capture_output=True, text=True, timeout=15)
    except Exception:
        pass  # the alert path being down is not this alarm's failure to report


def raise_alert(name, path, pct, rule, prio="high"):
    key = f"disk-pct:{name}"
    title = f"Disk {pct}% full: {name}"
    msg = (f"{path} is {pct}% used (raise threshold {rule['raise_at_or_above']}%). "
           f"wq#3171 — check for prunable/movable data before it fills.")
    aq = aq_mod()
    aq.ingest(source=SOURCE, key=key, title=title, message=msg, priority=prio)
    ntfy(title, msg, prio)
    print(f"RAISE {key}: {pct}%")


def clear_alert(name, note):
    """Edge-clear: resolve any open aq row for this mount's key, one RECOVERED ntfy."""
    key = f"disk-pct:{name}"
    aq = aq_mod()
    c = aq.db()
    ids = [r[0] for r in c.execute(
        "SELECT id FROM alerts WHERE source=? AND key=? AND status='open'",
        (SOURCE, key)).fetchall()]
    for rid in ids:
        c.execute("UPDATE alerts SET status='resolved', note=?, resolved_ts=? WHERE id=?",
                  (note, aq.now(), rid))
    c.commit()
    c.close()
    if ids:
        ntfy(f"Disk usage RECOVERED: {name}", note, "default")
        print(f"CLEAR {key} ({len(ids)} row(s))")
    return len(ids)


def load_state():
    try:
        with open(os.path.join(STATE_DIR, "state.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


# ------------------------------------------------------------------ probe ---
def disk_usage_pct(name, path):
    """Returns (pct_used, error). error is None on success. A probe failure
    (missing mount, permission) never counts as a clear: the caller must carry
    the prior state forward, same contract as aer_storm_alarm's prober."""
    try:
        du = shutil.disk_usage(path)
    except OSError as e:
        return None, f"{type(e).__name__}: {e}"
    if du.total == 0:
        return None, "zero-size filesystem"
    return round(du.used / du.total * 100, 1), None


# ------------------------------------------------------------- edge logic ---
def next_state(firing, pct, rule):
    """One mount's alarm state after one sample. `pct is None` (probe failed)
    carries the prior state forward."""
    if pct is None:
        return firing
    if firing:
        return pct >= rule["clear_below"]
    return pct >= rule["raise_at_or_above"]


def check(cfg, dry_run=False, prober=None):
    """One pass: probe every configured mount, edge-decide, act (unless
    dry_run). Returns the per-mount verdicts."""
    state = load_state()
    prober = prober or disk_usage_pct
    out = []
    for name, m in cfg["mounts"].items():
        rule = mount_rule(cfg, name, m)
        prior = bool(state.get(name, {}).get("firing", False))
        pct, err = prober(name, m["path"])
        firing = next_state(prior, pct, rule)
        v = {"mount": name, "path": m["path"], "pct": pct, "error": err,
             "was_firing": prior, "firing": firing}
        if not dry_run and firing != prior:
            if firing:
                raise_alert(name, m["path"], pct, rule)
            else:
                clear_alert(name, f"disk-pct-alarm: cleared, {pct}% "
                                   f"(< {rule['clear_below']}%)")
        state.setdefault(name, {})["firing"] = firing
        out.append(v)
    if not dry_run:
        save_state(state)
    return out


# ------------------------------------------------------------------- CLI ---
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    c.add_argument("--dry-run", action="store_true")

    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "check":
        verdicts = check(cfg, dry_run=a.dry_run)
        if a.json:
            print(json.dumps(verdicts))
        else:
            for v in verdicts:
                print(f"{v['mount']}: {v['pct']}% firing={v['firing']} "
                      f"(was={v['was_firing']}) err={v['error']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
