#!/usr/bin/env python3
"""wq#4460 acceptance probe: prove the liveness check can still FAIL.

Scope parameter of the fix is --live (default: /home/lumen/agents/fleet-selfheal/
selfheal.yaml, the file wq#4460 merged into). A probe run only against that file
can only confirm, so both inputs here come from OUTSIDE it:

  P1  --live = the rev-pinned seed (commit fb72bfaf) with nodes.notes.farida
      deleted  -> must print MISS nodes.notes.farida, rc 1
  P2  --live = the immutable pre-merge backup the merge tool wrote
      (selfheal.yaml.bak.20261022T034603) -> must print the original 4 MISS lines, rc 1
  P3  --live = the live file after the merge -> rc 0 (the result being claimed)

Seed for every run is the rev-pinned copy, so the probe is re-runnable.
"""
import subprocess
import sys

import yaml
from ruamel.yaml import YAML

MERGE = "/home/lumen/worktrees/wq4460-a1/ops/wq3354-fleet-selfheal/selfheal_merge.py"
SEED = "/tmp/fworker-wq4460/seed_pinned.yaml"
HOLED = "/tmp/fworker-wq4460/live_no_farida.yaml"
BACKUP = "/home/lumen/agents/fleet-selfheal/selfheal.yaml.bak.20261022T034603"
LIVE = "/home/lumen/agents/fleet-selfheal/selfheal.yaml"

rt = YAML()
rt.preserve_quotes = True
with open(SEED) as fh:
    doc = rt.load(fh)
del doc["nodes"]["notes"]["farida"]
with open(HOLED, "w") as fh:
    rt.dump(doc, fh)


def check(live):
    p = subprocess.run([sys.executable, MERGE, "--check", "--seed", SEED, "--live", live],
                       capture_output=True, text=True)
    miss = sorted(l.split(": ", 1)[1] for l in p.stdout.splitlines() if l.startswith("MISS "))
    return p.returncode, miss


ok = True
rc, miss = check(HOLED)
print(f"P1 holed-pinned-seed rc={rc} miss={miss}")
ok &= (rc == 1 and miss == ["nodes.notes.farida"])

rc, miss = check(BACKUP)
print(f"P2 pre-merge-backup rc={rc} miss={miss}")
ok &= (rc == 1 and miss == ["nodes.notes.apep", "nodes.notes.farida",
                            "nodes.notes.isis", "nodes.notes.khepri"])

rc, miss = check(LIVE)
print(f"P3 live-after-merge rc={rc} miss={miss}")
ok &= (rc == 0 and miss == [])

# the merge must not have silenced farida: notes is not a suppressor
nodes = yaml.safe_load(open(LIVE))["nodes"]
silenced = "farida" in (nodes.get("ignore") or []) or "farida" in (nodes.get("sleepers") or [])
print(f"P4 farida still alerting (not in ignore/sleepers): {not silenced}")
ok &= not silenced

print("PROBE PASS" if ok else "PROBE FAIL")
sys.exit(0 if ok else 1)
