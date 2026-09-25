#!/usr/bin/env python3
"""dc-endpoint-parity-check.py — wq#2613 nightly DC endpoint parity check.

Per MCP_CONTROL_PLANE_HA_DESIGN.md §2 ("nightly parity check (versions/config)")
and §3.2/§3.3: every endpoint node (lumen, anubis, bes, nephthys) is supposed to
run byte-identical supergateway/Desktop Commander versions, the same
dc-enforce-config.py tier-1 blocklist, an active mcp-gateway-watchdog.timer, and
unit drop-ins (dc-gateway-hardening.conf, dc-gateway-logging.conf) matching
Lumen's — Lumen is pref 1 and this script's baseline, not a fifth thing to
independently verify against a spec file. Divergence on any axis is exactly the
"liveness != health" class of defect §1 names (a node can be up, tunnel
connected, and still silently drifted).

Run nightly (dc-endpoint-parity-check.timer, this directory) as the `lumen`
user, which is the one identity in the design with real SSH to every node
(§3.2: "Each DC node has ~/.ssh keys for lumen@... and every fleet node,
BatchMode-tested"). NOT run through fleet-exec — that broker is a BUILD-lane
sandbox restriction (kb/ISSUE-LOG.md #740/#... , this ticket's README), not a
property of the real `lumen` account on the real host.

Drift -> `aq add dc-parity <node>.<axis> "<title>" "<detail>"` (idempotent:
aq upserts on matching (source,key), so a still-drifted node does not spam
new rows night after night — see /home/lumen/bin/aq's ingest()).
"""
import json
import subprocess
import sys
import datetime
from pathlib import Path

AQ = "/home/lumen/bin/aq"
SOURCE = "dc-parity"

# pref order matches MCP_CONTROL_PLANE_HA_DESIGN.md §3.1's MCP_BACKENDS order.
# host=None means "local" (checks run directly, no ssh hop) since this script
# runs ON lumen. Other hosts are tailscale MagicDNS names, not raw IPs --
# matching ops/wq2608-dc-endpoint-bes-a1/ssh_fleet_keys.sh's own convention
# of bare hostnames for every node except lumen (which has its IP pinned
# because it's the one every *other* node dials home to).
NODES = [
    {"name": "lumen", "host": None, "user": "lumen", "pref": 1},
    {"name": "anubis", "host": "anubis", "user": "anubis", "pref": 2},
    {"name": "bes", "host": "bes", "user": "bes", "pref": 3},
    {"name": "nephthys", "host": "nephthys", "user": "nephthys", "pref": 4},
]

GATEWAY_UNIT = {"lumen": "desktop-commander-mcp", "anubis": "dc-gateway", "bes": "dc-gateway", "nephthys": "dc-gateway"}
WATCHDOG_TIMER = "mcp-gateway-watchdog.timer"
DROPIN_FILES = ["dc-gateway-hardening.conf", "dc-gateway-logging.conf"]
SUPERGATEWAY_PKG = "supergateway"
DC_PKG = "@wonderwhy-er/desktop-commander"


def run(host, user, cmd, timeout=15):
    """Run cmd locally (host=None) or over ssh BatchMode. Returns (ok, stdout)."""
    if host is None:
        full = cmd
    else:
        full = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{user}@{host}", " ".join(cmd)]
    try:
        p = subprocess.run(full if host is None else full, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout.strip()
    except Exception as e:
        return False, str(e)


def npm_version(node, pkg):
    ok, out = run(node["host"], node["user"], ["npm", "ls", "-g", pkg, "--depth=0", "--json"])
    if not ok:
        return None
    try:
        return json.loads(out)["dependencies"][pkg]["version"]
    except Exception:
        return None


def enforce_config_output(node):
    home = "/home/lumen" if node["name"] == "lumen" else f"/home/{node['name']}"
    ok, out = run(node["host"], node["user"], ["python3", f"{home}/bin/dc-enforce-config.py"])
    return out if ok else None


def watchdog_active(node):
    ok, out = run(node["host"], node["user"], ["systemctl", "is-active", WATCHDOG_TIMER])
    return ok and out.strip() == "active"


def dropin_hashes(node):
    unit = GATEWAY_UNIT[node["name"]]
    hashes = {}
    for f in DROPIN_FILES:
        ok, out = run(node["host"], node["user"],
                       ["sha256sum", f"/etc/systemd/system/{unit}.service.d/{f}"])
        hashes[f] = out.split()[0] if ok and out else None
    return hashes


def check_node(node, baseline):
    findings = []
    sg = npm_version(node, SUPERGATEWAY_PKG)
    dc = npm_version(node, DC_PKG)
    if sg != baseline["supergateway"]:
        findings.append(("version.supergateway", f"supergateway={sg!r} baseline(lumen)={baseline['supergateway']!r}"))
    if dc != baseline["dc"]:
        findings.append(("version.dc", f"desktop-commander={dc!r} baseline(lumen)={baseline['dc']!r}"))

    cfg_out = enforce_config_output(node)
    if cfg_out != baseline["cfg_out"]:
        findings.append(("dc-enforce-config", f"output={cfg_out!r} baseline(lumen)={baseline['cfg_out']!r}"))

    if not watchdog_active(node):
        findings.append(("watchdog", f"{WATCHDOG_TIMER} is not active on {node['name']}"))

    hashes = dropin_hashes(node)
    for f, h in hashes.items():
        base_h = baseline["dropins"].get(f)
        if h != base_h:
            findings.append((f"dropin.{f}", f"sha256={h!r} baseline(lumen)={base_h!r}"))

    return findings


def aq_add(key, title, detail):
    subprocess.run([AQ, "add", SOURCE, key, title, detail, "--owner", "fleet-ops"], check=False)


def main():
    lumen = NODES[0]
    baseline = {
        "supergateway": npm_version(lumen, SUPERGATEWAY_PKG),
        "dc": npm_version(lumen, DC_PKG),
        "cfg_out": enforce_config_output(lumen),
        "dropins": dropin_hashes(lumen),
    }

    report = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "nodes": {}}
    any_drift = False
    for node in NODES[1:]:
        findings = check_node(node, baseline)
        report["nodes"][node["name"]] = {"drift": bool(findings), "findings": findings}
        for axis, detail in findings:
            any_drift = True
            aq_add(f"{node['name']}.{axis}", f"DC parity drift: {node['name']}/{axis}", detail)

    print(json.dumps(report, indent=2))
    return 1 if any_drift else 0


if __name__ == "__main__":
    sys.exit(main())
