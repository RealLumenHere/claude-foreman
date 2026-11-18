#!/usr/bin/env python3
"""verify_topology — read-only check that Commander is carried by Lumen (wq#4921).

Runs ON Lumen. Exit 0 iff every check passes:
  1. dc-gateway.service and cloudflared-mcp.service are active and enabled.
  2. cloudflared's metrics endpoint reports >= --min-conns edge connections.
  3. The gateway answers MCP initialize + tools/list on --gateway-url.

Touches nothing: systemctl is-active/is-enabled, one GET on the metrics port, two
POSTs to the gateway. Safe to run unattended (commander.rule_no_unattended covers
changes and failure tests, not reads).

    python3 verify_topology.py
    python3 verify_topology.py --min-conns 4 --json
"""

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

UNITS = ("dc-gateway.service", "cloudflared-mcp.service")
METRICS_URL = "http://localhost:20242/metrics"
GATEWAY_URL = "http://127.0.0.1:3100/mcp"
HA_RE = re.compile(r"^cloudflared_tunnel_ha_connections\s+([0-9.]+)\s*$", re.M)

_PROBE_PATH = Path(__file__).resolve().parent.parent / "wq3294-commander-ha" / "mcp_probe.py"


def _load_mcp_probe():
    spec = importlib.util.spec_from_file_location("mcp_probe", _PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def systemctl(verb: str, unit: str) -> str:
    r = subprocess.run(
        ["systemctl", verb, unit], capture_output=True, text=True, timeout=10
    )
    return r.stdout.strip() or r.stderr.strip()


def fetch(url: str, timeout_s: float) -> str:
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        return resp.read(1 << 20).decode("utf-8", "replace")


def ha_connections(metrics_text: str):
    m = HA_RE.search(metrics_text)
    return int(float(m.group(1))) if m else None


def check_units(units=UNITS, run=systemctl):
    out = []
    for unit in units:
        active, enabled = run("is-active", unit), run("is-enabled", unit)
        ok = active == "active" and enabled == "enabled"
        out.append((f"unit:{unit}", ok, f"{active}/{enabled}"))
    return out


def check_tunnel(min_conns: int, metrics_url=METRICS_URL, get=fetch, timeout_s=5.0):
    try:
        n = ha_connections(get(metrics_url, timeout_s))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return ("tunnel:ha_connections", False, f"metrics unreachable: {e}")
    if n is None:
        return ("tunnel:ha_connections", False, "metric absent")
    return ("tunnel:ha_connections", n >= min_conns, f"{n} (min {min_conns})")


def check_gateway(gateway_url=GATEWAY_URL, timeout_s=15.0, probe=None):
    probe = probe or _load_mcp_probe()
    ok = all(probe.call_ok(gateway_url, body, timeout_s) for body in probe.CALLS)
    return ("gateway:mcp", ok, gateway_url)


def run_checks(args, run=systemctl, get=fetch, probe=None):
    results = check_units(run=run)
    results.append(check_tunnel(args.min_conns, args.metrics_url, get))
    results.append(check_gateway(args.gateway_url, args.timeout, probe))
    return results


def main(argv=None, run=systemctl, get=fetch, probe=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-conns", type=int, default=1,
                    help="minimum cloudflared edge connections (healthy replica = 4)")
    ap.add_argument("--metrics-url", default=METRICS_URL)
    ap.add_argument("--gateway-url", default=GATEWAY_URL)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    results = run_checks(args, run, get, probe)
    ok = all(r[1] for r in results)
    if args.json:
        print(json.dumps({"ok": ok, "checks": [
            {"name": n, "ok": o, "detail": d} for n, o, d in results]}))
    else:
        for name, o, detail in results:
            print(f"{'OK  ' if o else 'FAIL'} {name}: {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
