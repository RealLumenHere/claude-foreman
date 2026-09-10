#!/usr/bin/env python3
"""wq#1654 — fail unit-drift when a timer's Description still claims it is
off (DISABLED/STAGED/Frank-gated) while systemd reports it enabled+active.

Source evidence: FLEET_SERVICE_ATLAS.md §7 F-3 (wq#1627) — lumen-familiarity,
lumen-model-audit, lumen-skillhex, lumen-memory-audit, lumen-judgearena-bench,
lumen-pelican-bench, lumen-foreman-dispatch/health/selfheal are all
enabled+active with a Description still claiming otherwise. The label lied to
Frank once already (REPEAT class); this check makes the drift loud instead of
silent.

  label_drift_check.py check          # live systemctl, exit 1 on violation
  label_drift_check.py check --json   # machine-readable violations
"""
import argparse
import json
import re
import subprocess
import sys

STALE_LABEL_RE = re.compile(r"DISABLED|STAGED|Frank-gated", re.IGNORECASE)
PROPS = ("Id", "Description", "ActiveState", "UnitFileState")


def list_timer_units():
    out = subprocess.run(
        ["systemctl", "list-units", "--type=timer", "--all", "--no-legend", "--plain"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def show_properties(unit_names):
    """One batched `systemctl show` call; returns {unit: {prop: value}}."""
    if not unit_names:
        return {}
    out = subprocess.run(
        ["systemctl", "show", *unit_names, "--property=" + ",".join(PROPS)],
        capture_output=True, text=True, check=True,
    ).stdout
    units, cur = {}, {}
    for line in out.splitlines():
        if not line.strip():
            if cur.get("Id"):
                units[cur["Id"]] = cur
            cur = {}
            continue
        key, _, value = line.partition("=")
        cur[key] = value
    if cur.get("Id"):
        units[cur["Id"]] = cur
    return units


def live_timers():
    return list(show_properties(list_timer_units()).values())


def violations(units):
    """units: iterable of dicts with Description/ActiveState/UnitFileState/Id.
    Returns the subset that is enabled+active with a stale off-label."""
    out = []
    for u in units:
        if u.get("ActiveState") != "active" or u.get("UnitFileState") != "enabled":
            continue
        if STALE_LABEL_RE.search(u.get("Description", "")):
            out.append(u)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="scan live timers for stale off-labels")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        bad = violations(live_timers())
        if args.json:
            print(json.dumps(bad, indent=2, sort_keys=True))
        elif bad:
            print("label-drift: %d timer(s) enabled+active with stale off-label:" % len(bad))
            for u in bad:
                print("  %-40s %s" % (u["Id"], u["Description"]))
        else:
            print("label-drift: CLEAN")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
