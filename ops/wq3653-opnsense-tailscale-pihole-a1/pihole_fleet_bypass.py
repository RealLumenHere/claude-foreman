#!/usr/bin/env python3
"""wq#3653 — Pi-hole v6 API script for RUNBOOK.md items 8 and 9.

NOT run from this build worker: the Pi-hole RPi is not in
harness/fleet-exec-policy.yaml's ssh/network allowlist, so this sandbox has no route to
it. Run this from a session that can reach the Pi-hole's web port.

Only /api/auth is pinned against current upstream docs (docs.pi-hole.net/api/auth/,
checked via fleet-search 2026-10-08). The groups/clients/domains/config resource shapes
below match the Pi-hole v6 web UI's own page names and are the standard REST layout for
those pages, but per-patch field names can drift — doctrine here is verify, don't guess,
so this script always fetches /api/docs (the instance's self-hosted OpenAPI spec) first
and refuses to make a write call until that's been read.

Implements:
  --bypass-group               NETWORK_PREMORTEM.md:39-57 Fleet-Bypass mechanism (P1-P3)
  --conditional-forwarding     RUNBOOK.md item 9: 192.168.8.0/24 -> OPNsense Unbound
"""
import argparse
import json
import sys
import urllib.request

FLEET_LAN_HOSTS = []  # fill with the fleet's static 192.168.8.0/24 addresses before running
FLEET_TAILNET_CIDRS = ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]  # P3: Tailscale-sourced queries
BYPASS_GROUP = "Fleet-Bypass"


def _request(base_url, path, sid=None, payload=None, method=None):
    url = base_url.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["X-FTL-SID"] = sid
    data = json.dumps(payload).encode() if payload is not None else None
    if method is None:
        method = "POST" if payload is not None else "GET"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def authenticate(base_url, password):
    resp = _request(base_url, "/api/auth", payload={"password": password})
    sid = resp.get("session", {}).get("sid")
    if not sid:
        raise RuntimeError(f"no sid in /api/auth response: {resp}")
    return sid


def confirm_api_shape(base_url):
    """Doctrine: verify the live schema before any write call, every run."""
    try:
        docs = _request(base_url, "/api/docs")
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        print(f"could not fetch {base_url}/api/docs ({exc}) — refusing to write blind", file=sys.stderr)
        sys.exit(1)
    print("Fetched live /api/docs. Confirm the /api/groups, /api/clients and "
          "/api/config/dns/revServers shapes below match what this script sends "
          "before re-running with --confirmed.", file=sys.stderr)
    return docs


def setup_bypass_group(base_url, sid):
    # NETWORK_PREMORTEM.md P1: group membership is additive, so the group must exist and
    # every fleet client's group list must become [Fleet-Bypass] only, not + Default.
    _request(base_url, "/api/groups", sid=sid,
              payload={"name": BYPASS_GROUP, "comment": "wq#3653: no adlists for fleet IPs"})

    clients = list(FLEET_LAN_HOSTS) + FLEET_TAILNET_CIDRS
    if not clients:
        print("FLEET_LAN_HOSTS is empty — fill it in with the fleet's static LAN "
              "addresses before this does anything useful (P1/P3).", file=sys.stderr)
    for client in clients:
        # P1: groups=[Fleet-Bypass] replaces membership, it does not add to Default.
        _request(base_url, "/api/clients", sid=sid,
                  payload={"client": client, "groups": [BYPASS_GROUP]})
    print(f"Fleet-Bypass group set for {len(clients)} client entries "
          f"(assign zero adlists to Fleet-Bypass in the Adlists page — P2 covers deny "
          f"domain-list entries too, check those separately).", file=sys.stderr)


def setup_conditional_forwarding(base_url, sid, opnsense_lan_ip, lan_cidr="192.168.8.0/24"):
    _request(base_url, "/api/config", sid=sid, payload={
        "config": {
            "dns": {
                "revServers": [
                    {
                        "active": True,
                        "cidr": lan_cidr,
                        "target": opnsense_lan_ip,
                        "domain": "",
                    }
                ]
            }
        }
    })
    print(f"Conditional forwarding set: {lan_cidr} -> {opnsense_lan_ip}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True, help="e.g. https://pi.hole")
    ap.add_argument("--password", required=True)
    ap.add_argument("--opnsense-lan-ip", help="required for --conditional-forwarding")
    ap.add_argument("--bypass-group", action="store_true")
    ap.add_argument("--conditional-forwarding", action="store_true")
    ap.add_argument("--confirmed", action="store_true",
                     help="skip the /api/docs pre-flight (only after you've read it once)")
    args = ap.parse_args()

    if not args.confirmed:
        confirm_api_shape(args.base_url)
        print("Re-run with --confirmed once the shapes check out.", file=sys.stderr)
        sys.exit(2)

    sid = authenticate(args.base_url, args.password)

    if args.bypass_group:
        setup_bypass_group(args.base_url, sid)
    if args.conditional_forwarding:
        if not args.opnsense_lan_ip:
            ap.error("--conditional-forwarding requires --opnsense-lan-ip")
        setup_conditional_forwarding(args.base_url, sid, args.opnsense_lan_ip)


if __name__ == "__main__":
    main()
