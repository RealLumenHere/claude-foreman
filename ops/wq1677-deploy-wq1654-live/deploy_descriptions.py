#!/usr/bin/env python3
"""wq#1677 — apply wq#1654's corrected Description= text to the 9 live
timers, via the git-tracked /home/lumen/fleet-units baseline, so the fix
does not itself register as unit-drift (README.md, wq1654-timer-label-drift
"Scope note"). Source of the nine (unit, corrected_description) pairs:
ingest_decisions.DECISIONS.

Root-only for the live-disk half (writes under /etc/systemd/system); the
baseline re-snapshot that follows must run as `lumen`, not `--root` — git's
dubious-ownership check trips on a euid mismatch against the lumen-owned
fleet-units repo (wq#1774 REPORT.md). Run via:

  /home/lumen/bin/lumen-run start --root -- \\
    python3 deploy_descriptions.py apply
  python3 /home/lumen/bin/unit-drift snapshot     # as lumen: re-baseline

Idempotent: a unit whose live Description= already matches DECISIONS is
left untouched, so a killed-and-restarted run never double-applies and a
routine re-run reports no changes.
"""
import argparse
import os
import re
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "wq1654-timer-label-drift"),
)
import ingest_decisions as decisions  # noqa: E402

DESC_RE = re.compile(r"(?m)^Description=.*$")


def rewrite_description(text, corrected_description):
    """Return (new_text, changed) with the unit's Description= line set to
    corrected_description. No-ops if the line already matches."""
    new_line = "Description=" + corrected_description
    if DESC_RE.search(text) is None:
        raise ValueError("no Description= line found")
    new_text = DESC_RE.sub(new_line, text, count=1)
    return new_text, new_text != text


def apply(sys_dir):
    """Apply every DECISIONS correction under sys_dir. Returns the list of
    unit names actually rewritten (unchanged units are skipped)."""
    changed = []
    for unit, _decision, corrected_description, _rationale in decisions.DECISIONS:
        path = os.path.join(sys_dir, unit)
        with open(path) as f:
            text = f.read()
        new_text, did_change = rewrite_description(text, corrected_description)
        if did_change:
            with open(path, "w") as f:
                f.write(new_text)
            changed.append(unit)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["apply"])
    ap.add_argument("--sys-dir", default="/etc/systemd/system")
    args = ap.parse_args(argv)

    changed = apply(args.sys_dir)
    if changed:
        print("deploy_descriptions: rewrote %d unit(s): %s"
              % (len(changed), ", ".join(changed)))
    else:
        print("deploy_descriptions: nothing to do — all %d units already match"
              % len(decisions.DECISIONS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
