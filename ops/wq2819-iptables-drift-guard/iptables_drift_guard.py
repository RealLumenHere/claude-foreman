#!/usr/bin/env python3
"""wq#2819 -- reconcile /etc/iptables/rules.v4 against the live ruleset.

Root cause (see README.md, Audit): dockerd manages its own raw-table
PREROUTING rules -- one DROP per container port published to a specific
host IP (loopback-only `-d 127.0.0.1 ! -i lo`, or bridge-scoped
`-d 172.x ! -i br-*`) -- and re-inserts them whenever it reconciles
container networking (compose up, container restart, daemon restart).
dockerd never calls netfilter-persistent, so /etc/iptables/rules.v4 drifts
behind live state until an operator remembers to save. Same class as
wq#2808's tailscaled/ts-input finding, different daemon, no fixed
schedule -- so a one-time save (wq#2808's apply.sh) does not close it.

This runs on a timer, diffs live vs persisted, and if they differ, saves
and alerts. It never touches the live ruleset itself.
"""
import argparse
import datetime
import json
import re
import subprocess
import sys

RULES_FILE = "/etc/iptables/rules.v4"
STATE_FILE = "/home/lumen/logs/.lumen-iptables-drift-guard.state.json"
NTFY = "/home/lumen/bin/ntfy-send"

_COUNTER_RE = re.compile(r"\[\d+:\d+\]")


def normalize(text: str) -> list[str]:
    """Drop comment lines and zero out packet/byte counters, which churn on
    every packet and carry no rule-identity information."""
    lines = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        lines.append(_COUNTER_RE.sub("[0:0]", line))
    return lines


def diff_lines(live_text: str, persisted_text: str) -> tuple[list[str], list[str]]:
    """Return (only_live, only_persisted): normalized lines present in one
    side but not the other. Order-independent, duplicate-count-independent
    (these are guard rules; a rule either is or isn't present)."""
    live_set = set(normalize(live_text))
    persisted_set = set(normalize(persisted_text))
    only_live = sorted(live_set - persisted_set)
    only_persisted = sorted(persisted_set - live_set)
    return only_live, only_persisted


def live_rules() -> str:
    return subprocess.run(
        ["iptables-save"], capture_output=True, text=True, check=True
    ).stdout


def persisted_rules() -> str:
    with open(RULES_FILE) as f:
        return f.read()


def save() -> None:
    subprocess.run(["netfilter-persistent", "save"], check=True)


def notify(title: str, msg: str) -> None:
    subprocess.run([NTFY, "-t", "lumen-alerts", "-T", title, msg], check=False)


def _write_state(d: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(d, f)
    except OSError:
        pass


def run(dry_run: bool = False) -> int:
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    live = live_rules()
    persisted = persisted_rules()
    only_live, only_persisted = diff_lines(live, persisted)

    if not only_live and not only_persisted:
        _write_state({"ts": ts, "drift": False})
        print("OK: persisted == live")
        return 0

    print(f"DRIFT: {len(only_live)} live-only rule(s), {len(only_persisted)} persisted-only rule(s)")
    for l in only_live:
        print(f"  +live   {l}")
    for l in only_persisted:
        print(f"  -stale  {l}")

    if dry_run:
        _write_state({"ts": ts, "drift": True, "saved": False, "dry_run": True})
        return 3

    save()
    live2 = live_rules()
    persisted2 = persisted_rules()
    only_live2, only_persisted2 = diff_lines(live2, persisted2)
    if only_live2 or only_persisted2:
        notify(
            "wq2819 iptables drift NOT resolved",
            f"netfilter-persistent save ran but {len(only_live2)} rule(s) are still live-only "
            f"and {len(only_persisted2)} still persisted-only -- check journalctl -u lumen-iptables-drift-guard",
        )
        _write_state({"ts": ts, "drift": True, "saved": False})
        return 4

    notify(
        "wq2819 iptables drift saved",
        f"{RULES_FILE} was behind live by {len(only_live)} rule(s) (docker/loopback guards); saved and now matches",
    )
    _write_state({"ts": ts, "drift": True, "saved": True, "reconciled": only_live})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["run"])
    ap.add_argument("--dry-run", action="store_true", help="report drift, do not save or alert")
    args = ap.parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
