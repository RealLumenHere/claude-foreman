#!/usr/bin/env python3
"""wq#4640 end-to-end: the DEPLOYED sweep code, reading the LIVE config, folds the
merged toilet note into the page body it would file.

Imports sh_nodes.py from /home/lumen/agents/fleet-selfheal (not the repo copy) and
first asserts the deployed bytes equal the repo seed's bytes at rev 9f24db34, so a
pass here is about the code that actually runs on the timer. sweep() takes peers_fn
and facts_fn injected, so no tailscale call and no lumen_state call happens; ctx is a
recorder that captures file_once bodies instead of filing tickets. Nothing is written.

Controls, so the probe can fail: `nephthys` already had a note live before this
ticket (it must still fold -- the merge must not have clobbered the operator's copy),
and `gameboy-ctl` has no note at all (it must get no `known issue:` line).
"""
import hashlib
import importlib.util
import pathlib
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]
SEED_REL = "ops/wq3354-fleet-selfheal/selfheal.yaml"
SEED_REV = "9f24db34"
DEPLOY = pathlib.Path("/home/lumen/agents/fleet-selfheal")
LIVE = DEPLOY / "selfheal.yaml"
NOW = 1_800_000_000


def load_deployed():
    rel = "ops/wq3354-fleet-selfheal/sh_nodes.py"
    repo_bytes = subprocess.run(["git", "-C", str(REPO), "show", f"{SEED_REV}:{rel}"],
                                capture_output=True, check=True).stdout
    dep = DEPLOY / "sh_nodes.py"
    same = hashlib.sha256(dep.read_bytes()).hexdigest() == \
        hashlib.sha256(repo_bytes).hexdigest()
    print(f"deployed sh_nodes.py == repo@{SEED_REV}: {same}")
    sys.path.insert(0, str(DEPLOY))
    spec = importlib.util.spec_from_file_location("sh_nodes_deployed", dep)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, same


class Ctx:
    """Minimal recorder standing in for the sweep context."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.now = NOW
        self._sections = {"nodes": {}, "nodes_meta": {"baselined": True},
                          "tickets": {}}
        self.filed = {}
        self.notes = []

    def section(self, name):
        return self._sections.setdefault(name, {})

    def note(self, cls, msg):
        self.notes.append(msg)

    def resolve(self, cls, key, msg):
        self.notes.append(f"resolve {key}: {msg}")

    def file_once(self, cls, key, title, body, page=None):
        self.filed[key] = body
        return 0           # 0 == "wq add did not run", like a dry-run

    def runner(self, argv, timeout=None):
        raise AssertionError(f"probe must not shell out: {argv}")


# Synthetic tailnet IPs from TEST-NET-2 (RFC 5737): node_for() only needs peers and
# facts to agree, so these are fixture values, NOT claims about any node's real IP.
CASES = {
    "toilet": "198.51.100.1",          # the note this ticket deployed
    "nephthys": "198.51.100.2",        # note already live pre-merge (must survive)
    "no-note-probe": "198.51.100.3",   # no note at all (must get no known-issue line)
}


def main():
    mod, code_same = load_deployed()
    cfg = yaml.safe_load(LIVE.read_text())
    ctx = Ctx(cfg)

    peers = {}
    facts = {}
    for node, ip in CASES.items():
        peers[f"peer:{node}"] = {"online": False, "last_seen": "", "ips": [ip],
                                 "host": f"{node}-probe"}
        facts[node] = {"tailscale_ip": ip}
        ctx.section("nodes")[f"peer:{node}"] = {"offline_since": NOW - 3600}

    mod.sweep(ctx, peers_fn=lambda c: peers, facts_fn=lambda c: facts)

    notes = cfg["nodes"]["notes"]
    fails = [] if code_same else ["deployed-code-differs"]
    for node in CASES:
        body = ctx.filed.get(f"node-offline:{node}")
        if body is None:
            print(f"  {node}: NO TICKET (expected one)")
            fails.append(node)
            continue
        want = f"known issue: {notes[node]}" if node in notes else None
        got = next((l.strip() for l in body.splitlines()
                    if l.strip().startswith("known issue:")), None)
        ok = (got == want.strip()) if want else (got is None)
        print(f"  {node}: note expected={bool(want)} folded={got is not None} ok={ok}")
        if not ok:
            fails.append(node)

    toilet = ctx.filed.get("node-offline:toilet", "")
    print(f"toilet body carries wq#4638 citation: {'wq#4638' in toilet}")
    print(f"toilet body carries opsaudit citation: {'opsaudit_a3_node_exporter' in toilet}")
    if "wq#4638" not in toilet:
        fails.append("toilet-citation")

    print("PROBE PASS" if not fails else f"PROBE FAIL: {','.join(fails)}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
