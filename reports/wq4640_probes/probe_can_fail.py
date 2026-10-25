#!/usr/bin/env python3
"""wq#4640 acceptance probe for the toilet nodes.notes liveness deploy.

The fix's scope parameter is selfheal_merge.py's `--live`, whose default/intended
input is /home/lumen/agents/fleet-selfheal/selfheal.yaml. Per wq#2630 a probe drawn
only from that population cannot fail, so P1 and P2 feed `--live` inputs from OUTSIDE
it and MUST come back rc=1 with the toilet miss:

  P1  live := the repo seed at rev 7e93b7a4 (the commit immediately before e7d2dd76
      added the toilet note) -> rc 1, MISS nodes.notes.toilet. Rev-pinned, re-runnable.
  P2  live := the pre-merge backup this ticket's merge wrote -> rc 1, same miss.
      An immutable file, never rewritten (name carries a timestamp).
  P3  live := the real live config after the merge -> rc 0.
  P4  toilet is in neither nodes.ignore nor nodes.sleepers live, i.e. the note is
      context and not a silencer: toilet still pages.
  P5  the live note is byte-identical to the rev-pinned seed note.

Seed side is pinned to rev 9f24db34 throughout, so no probe reads a mutable file in
another tree except the one live file that is the subject of the check.
"""
import pathlib
import subprocess
import sys
import tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
BUNDLE = REPO / "ops" / "wq3354-fleet-selfheal"
MERGE = BUNDLE / "selfheal_merge.py"
SEED_REL = "ops/wq3354-fleet-selfheal/selfheal.yaml"
SEED_REV = "9f24db34"          # seed as shipped when this probe was written
PRE_NOTE_REV = "7e93b7a4"      # tree before e7d2dd76 added nodes.notes.toilet
LIVE = pathlib.Path("/home/lumen/agents/fleet-selfheal/selfheal.yaml")
BACKUP = LIVE.with_name("selfheal.yaml.bak.20261025T215449")
NODE = "toilet"
KEYPATH = f"nodes.notes.{NODE}"


def show(rev, dest):
    out = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{SEED_REL}"],
                         capture_output=True, text=True, check=True).stdout
    dest.write_text(out)
    return dest


def check(seed, live):
    p = subprocess.run([sys.executable, str(MERGE), "--check",
                        "--seed", str(seed), "--live", str(live)],
                       capture_output=True, text=True)
    miss = [l.split(": ", 1)[1] for l in p.stdout.splitlines()
            if l.startswith("MISS seed key not live: ")]
    return p.returncode, miss


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        seed = show(SEED_REV, tmp / "seed.yaml")
        pre = show(PRE_NOTE_REV, tmp / "pre_note.yaml")

        rc, miss = check(seed, pre)
        print(f"P1 pre-note-seed-as-live  rc={rc} miss={miss}")
        if not (rc == 1 and miss == [KEYPATH]):
            fails.append("P1")

        rc, miss = check(seed, BACKUP)
        print(f"P2 pre-merge-backup       rc={rc} miss={miss}")
        if not (rc == 1 and miss == [KEYPATH]):
            fails.append("P2")

        rc, miss = check(seed, LIVE)
        print(f"P3 live-after-merge       rc={rc} miss={miss}")
        if not (rc == 0 and miss == []):
            fails.append("P3")

        live_doc = yaml.safe_load(LIVE.read_text())
        seed_doc = yaml.safe_load(seed.read_text())
        ncfg = live_doc["nodes"]
        silenced = NODE in set(ncfg.get("ignore") or []) or \
            NODE in set(ncfg.get("sleepers") or [])
        print(f"P4 {NODE} still alerting (not ignore/sleepers): {not silenced}")
        if silenced:
            fails.append("P4")

        same = ncfg["notes"][NODE] == seed_doc["nodes"]["notes"][NODE]
        print(f"P5 live note == seed@{SEED_REV} note: {same}")
        if not same:
            fails.append("P5")

    print("PROBE PASS" if not fails else f"PROBE FAIL: {','.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
