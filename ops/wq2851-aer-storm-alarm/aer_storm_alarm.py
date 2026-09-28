#!/usr/bin/env python3
"""aer_storm_alarm.py — MacPro6,1 PCIe AER-storm leading-indicator alarm (wq#2851).

wq#2776's drop recheck found nephthys losing power abruptly three times
(09-24/09-25/09-27), ollama idle each time (not load/thermal), and each loss
preceded within seconds by a PCIe AER storm on root port 00:01.0 and the
PLX PEX8723. Per-boot `journalctl -k 'PCIe Bus Error'` hourly counts: a
healthy 22h+ boot topped out at 5/hr; the three boots that then died ran
33-1986/hr, rising into the death. `power-remediate` (bin/power-remediate:56,
ELIGIBLE = {isis, hathor, khepri, nephthys} — the same four nodes this file
targets) is ARMED=0 and untouched by this alarm: this is notify-only, never
an automatic action (instinct 3 / ticket).

Every 5 min, over ssh, count kernel 'PCIe Bus Error' lines in the last 15 min
for each node. EDGE-triggered: one aq row + one ntfy on raise, the row is
resolved and one RECOVERED ntfy fires on clear, nothing repeats while a node
stays in the same state (no per-minute spam). Threshold and hysteresis live
in aer_storm_alarm.yaml, not here, so the number can be revised without a
code change; see REPORT.md for the replay that justifies the starting value.

  aer_storm_alarm.py check [--json] [--dry-run]      one live pass, all nodes
  aer_storm_alarm.py baseline [--hours N]             per-node hourly AER
                                                       counts over ssh, for
                                                       gathering the
                                                       hathor/isis/khepri
                                                       baselines the report
                                                       needs and that this
                                                       worker's grant could
                                                       not reach live
  aer_storm_alarm.py replay --log FILE --node NODE    replay a saved
                                                       `journalctl -k -o
                                                       short-iso` text dump
                                                       (e.g. a retained boot)
                                                       through the same
                                                       sliding-window rule
                                                       used by `check`, no
                                                       ssh, no aq, no ntfy
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aer_storm_alarm.yaml")
SOURCE = "aer-storm"
AQ_DIR = os.environ.get("AER_AQ_DIR", "/home/lumen/agents/alert-queue")
STATE_DIR = os.environ.get("AER_STATE_DIR", "/home/lumen/state/aer-storm-alarm")
NTFY_BIN = os.environ.get("AER_NTFY_BIN", "/home/lumen/bin/ntfy-send")
SSH_BIN = os.environ.get("AER_SSH_BIN", "ssh")
PATTERN = "PCIe Bus Error"

SSH_OPTS = ["-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]

# The same four MacPro6,1 nodes as bin/power-remediate's ELIGIBLE set and
# ops/fan_floor_alarm.yaml's `nodes:` block (the ticket's "not a second
# roster": same four names, same ssh-hostname convention as
# ops/wq2613-dc-endpoint-parity-check-a1/dc-endpoint-parity-check.py, bare
# tailscale MagicDNS hostname as the ssh host).
DEFAULT_NODES = {"isis": "isis", "hathor": "hathor", "khepri": "khepri", "nephthys": "nephthys"}


class ConfigError(ValueError):
    pass


def default_config():
    return {
        "rule": {"window_min": 15, "raise_above": 30, "clear_at_or_below": 15},
        "nodes": dict(DEFAULT_NODES),
    }


def validate(cfg):
    rule = cfg.get("rule") or {}
    for key in ("window_min", "raise_above", "clear_at_or_below"):
        if not isinstance(rule.get(key), (int, float)):
            raise ConfigError(f"rule.{key} missing or not a number")
    if rule["clear_at_or_below"] > rule["raise_above"]:
        raise ConfigError("rule.clear_at_or_below must not exceed rule.raise_above")
    nodes = cfg.get("nodes") or {}
    if not nodes:
        raise ConfigError("nodes must not be empty")
    return cfg


def load_config(path=CONFIG):
    if not os.path.exists(path):
        return default_config()
    import yaml
    with open(path) as fh:
        return validate(yaml.safe_load(fh))


# --------------------------------------------------------------- plumbing ---
def aq_mod():
    sys.path.insert(0, AQ_DIR)
    import aq
    return aq


def ntfy(title, msg, prio):
    try:
        subprocess.run([NTFY_BIN, "-p", prio, "-T", title, msg],
                       capture_output=True, text=True, timeout=15)
    except Exception:
        pass  # the alert path being down is not this alarm's failure to report


def raise_alert(node, count, window_min, prio="urgent"):
    key = f"aer-storm:{node}"
    title = f"AER storm: {node}"
    msg = (f"{count} '{PATTERN}' kernel lines in the last {window_min} min on {node}. "
           f"Leading indicator only, per wq#2776/wq#2851 — this is NOT the power loss "
           f"itself and takes no automatic action; power-remediate is untouched.")
    aq = aq_mod()
    aq.ingest(source=SOURCE, key=key, title=title, message=msg, priority=prio)
    ntfy(title, msg, prio)
    print(f"RAISE {key}: {count} in {window_min}min")


def clear_alert(node, note):
    """Edge-clear: resolve any open aq row for this node's key, one RECOVERED ntfy."""
    key = f"aer-storm:{node}"
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
        ntfy(f"AER storm RECOVERED: {node}", note, "default")
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
def count_via_ssh(node, host, window_min, ssh_bin=None, ssh_opts=None):
    """Count PATTERN kernel lines in the last window_min minutes, over ssh. Returns
    (count, error) — error is None on success, a short reason string otherwise. A
    probe failure never counts as a clear: the caller must carry the prior state."""
    cmd = [ssh_bin or SSH_BIN, *(ssh_opts or SSH_OPTS), host,
           f"journalctl -k --no-pager -q --since '-{int(window_min)}min'"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return None, f"ssh exit {r.returncode}: {(r.stderr or '').strip()[:200]}"
    return len(re.findall(re.escape(PATTERN), r.stdout)), None


# ------------------------------------------------------------- edge logic ---
def next_state(firing, count, rule):
    """One node's alarm state after one sample. `count is None` (probe failed)
    carries the prior state forward — a probe failure must never read as a clear."""
    if count is None:
        return firing
    if firing:
        return count > rule["clear_at_or_below"]
    return count > rule["raise_above"]


def check(cfg, dry_run=False, prober=None):
    """One pass: probe every node, edge-decide, act (unless dry_run). Returns the
    per-node verdicts, in the same shape whether or not an action fired."""
    rule = cfg["rule"]
    state = load_state()
    prober = prober or count_via_ssh
    out = []
    for node, host in cfg["nodes"].items():
        prior = bool(state.get(node, {}).get("firing", False))
        count, err = prober(node, host, rule["window_min"])
        firing = next_state(prior, count, rule)
        v = {"node": node, "count": count, "error": err, "was_firing": prior, "firing": firing}
        if not dry_run and firing != prior:
            if firing:
                raise_alert(node, count, rule["window_min"])
            else:
                clear_alert(node, f"aer-storm-alarm: cleared, {count} in last "
                                   f"{rule['window_min']}min (<= {rule['clear_at_or_below']})")
        state.setdefault(node, {})["firing"] = firing
        out.append(v)
    if not dry_run:
        save_state(state)
    return out


# ------------------------------------------------------------------ baseline/replay ---
def baseline_via_ssh(node, host, hours, ssh_bin=None, ssh_opts=None):
    """Hourly PATTERN counts over the last `hours` hours, oldest first. One ssh
    round trip per node: journalctl -o short-iso, bucketed here rather than one
    ssh call per hour."""
    cmd = [ssh_bin or SSH_BIN, *(ssh_opts or SSH_OPTS), host,
           f"journalctl -k -o short-iso --no-pager -q --since '-{int(hours)}hours'"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None, f"ssh exit {r.returncode}: {(r.stderr or '').strip()[:200]}"
    return bucket_hourly(r.stdout), None


TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}):\d{2}:\d{2}")


def bucket_hourly(journal_text):
    """{hour_iso: count} of PATTERN lines, from a `journalctl -o short-iso` dump."""
    buckets = {}
    for line in journal_text.splitlines():
        if PATTERN not in line:
            continue
        m = TS_RE.match(line)
        hour = m.group(1) if m else "unknown"
        buckets[hour] = buckets.get(hour, 0) + 1
    return dict(sorted(buckets.items()))


def replay(log_path, node, rule):
    """Slide the same window/threshold rule wq#2851's `check` uses over a saved
    `journalctl -k -o short-iso` text dump, and print every raise/clear transition.
    This is how a retained boot (e.g. nephthys boot -1/-3/-5, or the healthy boot 0
    control) gets checked against the alarm without waiting for it to recur live."""
    with open(log_path) as fh:
        lines = [ln for ln in fh if PATTERN in ln]
    stamps = []
    for ln in lines:
        m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", ln)
        if m:
            stamps.append(time.mktime(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")))
    stamps.sort()
    if not stamps:
        print(f"{node}: no '{PATTERN}' lines in {log_path}")
        return []
    window_s = rule["window_min"] * 60
    firing, out = False, []
    t = stamps[0]
    end = stamps[-1]
    step = 300  # 5-minute cadence, matching the live timer
    import bisect
    while t <= end:
        lo = bisect.bisect_left(stamps, t - window_s)
        hi = bisect.bisect_right(stamps, t)
        count = hi - lo
        now = next_state(firing, count, rule)
        if now != firing:
            firing = now
            out.append({"t": t, "state": "raise" if now else "clear", "count": count})
        t += step
    for e in out:
        print(f"{node} {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e['t']))} "
              f"{e['state']:5} count={e['count']}")
    return out


# ------------------------------------------------------------------- CLI ---
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=CONFIG)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    c.add_argument("--dry-run", action="store_true")

    b = sub.add_parser("baseline")
    b.add_argument("--hours", type=int, default=24)
    b.add_argument("--node")

    r = sub.add_parser("replay")
    r.add_argument("--log", required=True)
    r.add_argument("--node", required=True)

    a = ap.parse_args(argv)
    cfg = load_config(a.config)

    if a.cmd == "check":
        verdicts = check(cfg, dry_run=a.dry_run)
        for v in verdicts:
            print(json.dumps(v) if a.json else
                  f"{v['node']:9} count={v['count']} firing={v['firing']}"
                  f"{' error=' + v['error'] if v['error'] else ''}")
        return 0

    if a.cmd == "baseline":
        nodes = {a.node: cfg["nodes"][a.node]} if a.node else cfg["nodes"]
        for node, host in nodes.items():
            buckets, err = baseline_via_ssh(node, host, a.hours)
            if err:
                print(f"{node}: ERROR {err}")
                continue
            for hour, n in buckets.items():
                print(f"{node} {hour} {n}")
            if not buckets:
                print(f"{node}: 0 '{PATTERN}' lines in last {a.hours}h")
        return 0

    if a.cmd == "replay":
        replay(a.log, a.node, cfg["rule"])
        return 0


if __name__ == "__main__":
    sys.exit(main())
