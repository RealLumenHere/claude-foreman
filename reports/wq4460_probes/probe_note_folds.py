#!/usr/bin/env python3
"""wq#4460 end-to-end probe: the merged note reaches the actual page BODY.

Runs the DEPLOYED sweep code (/home/lumen/agents/fleet-selfheal) against the LIVE
config, in dry-run, with a COPY of state.json whose four node-offline keys are
removed (the live sweep holds those tickets open, so sh_nodes.py:178 correctly
declines to re-page and the body is never built). file_once is wrapped to capture
the body the next page would carry; dry_run keeps the real file_once from filing.
Live state.json is never opened for writing.

Pass: each of isis/khepri/farida/apep gets a body containing "known issue: " with
text identical to the live config's nodes.notes entry; and nephthys/ptah/sia (notes
that were already live before wq#4460) still fold -> the merge did not disturb them.
Discriminating control: a node with no note must NOT get a "known issue:" line.
(hathor is deliberately not a control: nodes.sleepers takes it out of the due list,
so it never builds a body.)
"""
import json
import shutil
import sys

sys.path.insert(0, "/home/lumen/agents/fleet-selfheal")
import selfheal_common as common  # noqa: E402
import selfheal_sweep as sweep  # noqa: E402
import yaml  # noqa: E402

CFG = "/home/lumen/agents/fleet-selfheal/selfheal.yaml"
LIVE_STATE = "/home/lumen/agents/fleet-selfheal/state.json"
COPY = "/tmp/fworker-wq4460/state_copy.json"
MERGED = ("isis", "khepri", "farida", "apep")

shutil.copy2(LIVE_STATE, COPY)
st = json.load(open(COPY))
for n in list(st.get("tickets", {})):
    if n.startswith("node-offline:"):
        st["tickets"].pop(n)
json.dump(st, open(COPY, "w"))

ctx = common.Ctx(common.load_config(CFG), common.load_state(COPY), dry_run=True)
pages = {}
real_file_once = ctx.file_once


def spy(cls, key, title, body, *a, **kw):
    pages[key] = body
    return real_file_once(cls, key, title, body, *a, **kw)


ctx.file_once = spy
sweep.run(ctx, {"nodes"})

notes = yaml.safe_load(open(CFG))["nodes"]["notes"]
ok = True
for node in MERGED + ("nephthys", "ptah", "sia"):
    body = pages.get(f"node-offline:{node}", "")
    folded = f"known issue: {notes[node]}" in body
    print(f"{node}: note folded into page body = {folded}")
    ok &= folded

unnoted = [n for n in ("gameboy", "opnsense-fw", "3dprinter") if f"node-offline:{n}" in pages]
for node in unnoted:
    has = "known issue:" in pages[f"node-offline:{node}"]
    print(f"{node} (no note in config): known-issue line absent = {not has}")
    ok &= not has
print("discriminating no-note control ran:", bool(unnoted))

print("live state.json untouched:",
      json.load(open(LIVE_STATE)).get("tickets", {}).get("node-offline:farida") is not None)
print("PROBE PASS" if ok else "PROBE FAIL")
sys.exit(0 if ok else 1)
