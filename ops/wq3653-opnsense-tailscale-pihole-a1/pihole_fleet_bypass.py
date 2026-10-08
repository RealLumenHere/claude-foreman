#!/usr/bin/env python3
"""wq#3653 — Pi-hole v6 API script for RUNBOOK.md items 8 and 9.

NOT run from this build worker: the Pi-hole RPi is not in
harness/fleet-exec-policy.yaml's ssh/network allowlist, so this sandbox has no route to
it. Run this from a session that can reach the Pi-hole's web port.

wq#3707: resource shapes below (groups/clients/config) are now pinned against
pi-hole/FTL's own source (src/api/docs/content/specs/{groups,clients,config}.yaml,
src/database/gravity-db.c, src/config/config.c), not pattern-matched off the web UI —
see foreman/reports/wq3700_judgment_wq3653_opnsense-tailscale-pihole-a1.md for the
citations. /api/docs itself is a RapiDoc UI (301 -> /api/docs/ -> index.html), not a
JSON document, so confirm_api_shape can only confirm it's reachable, not parse it.

Implements:
  --bypass-group               NETWORK_PREMORTEM.md:39-57 Fleet-Bypass mechanism (P1-P3)
  --conditional-forwarding     RUNBOOK.md item 9: 192.168.8.0/24 -> OPNsense Unbound
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

FLEET_LAN_HOSTS = []  # fill with the fleet's static 192.168.8.0/24 addresses before running
FLEET_TAILNET_CIDRS = ["100.64.0.0/10", "fd7a:115c:a1e0::/48"]  # P3: Tailscale-sourced queries
BYPASS_GROUP = "Fleet-Bypass"


def _request(base_url, path, sid=None, payload=None, method=None, parse_json=True):
    url = base_url.rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["X-FTL-SID"] = sid
    data = json.dumps(payload).encode() if payload is not None else None
    if method is None:
        method = "POST" if payload is not None else "GET"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read()
    return json.loads(body.decode()) if parse_json else body


def authenticate(base_url, password):
    resp = _request(base_url, "/api/auth", payload={"password": password})
    sid = resp.get("session", {}).get("sid")
    if not sid:
        raise RuntimeError(f"no sid in /api/auth response: {resp}")
    return sid


def confirm_api_shape(base_url):
    """Doctrine: verify the docs are reachable before any write call, every run.

    FTL's /api/docs is a RapiDoc UI, not a JSON document: a bare /api/docs 301s to
    /api/docs/, which serves index.html (src/api/docs/docs.c:15-37); the per-resource
    OpenAPI fragments are plain-text YAML at /api/docs/specs/*.yaml (docs.h), never a
    single parseable spec. This can only confirm the docs are up, not auto-verify
    shapes against them — it tells the operator where to look instead.
    """
    try:
        _request(base_url, "/api/docs/", parse_json=False)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        print(f"could not fetch {base_url}/api/docs/ ({exc}) — refusing to write blind", file=sys.stderr)
        sys.exit(1)
    print(f"{base_url}/api/docs/ is reachable. Open it in a browser and confirm the "
          "/api/groups, /api/clients and /api/config (specs/groups.yaml, "
          "specs/clients.yaml, specs/config.yaml) shapes match what this script sends "
          "before re-running with --confirmed.", file=sys.stderr)


def _upsert_group(base_url, sid, name, comment):
    # PUT /api/groups/{name} upserts by name (gravity-db.c: INSERT ... ON CONFLICT(name)
    # DO UPDATE) only when the payload omits "name" -- sending "name" here routes to the
    # rename branch (UPDATE ... WHERE name = :item) instead, which no-ops when the group
    # doesn't exist yet, so --confirmed on a fresh Pi-hole would silently create nothing.
    resp = _request(base_url, f"/api/groups/{urllib.parse.quote(name, safe='')}", sid=sid,
                     payload={"comment": comment}, method="PUT")
    groups = resp.get("groups") or []
    if not groups or "id" not in groups[0]:
        raise RuntimeError(f"no group id in PUT /api/groups/{name} response: {resp}")
    return groups[0]["id"]


def setup_bypass_group(base_url, sid):
    # NETWORK_PREMORTEM.md P1: group membership is additive, so the group must exist and
    # every fleet client's group list must become [Fleet-Bypass] only, not + Default.
    # clients.yaml: "groups" is an array of integer group IDs, not names -- the real id
    # has to come back from the API, never be guessed as the name string.
    group_id = _upsert_group(base_url, sid, BYPASS_GROUP, "wq#3653: no adlists for fleet IPs")

    clients = list(FLEET_LAN_HOSTS) + FLEET_TAILNET_CIDRS
    if not clients:
        print("FLEET_LAN_HOSTS is empty — fill it in with the fleet's static LAN "
              "addresses before this does anything useful (P1/P3).", file=sys.stderr)
    for client in clients:
        # PUT (not POST) /api/clients/{client}: upserts (gravity-db.c ON CONFLICT(ip) DO
        # UPDATE), so a re-run doesn't 400 on "already present". {client} must be
        # URI-encoded (clients.yaml) since tailnet CIDRs contain "/".
        # groups=[group_id] replaces membership outright, it does not add to Default.
        _request(base_url, f"/api/clients/{urllib.parse.quote(client, safe='')}", sid=sid,
                  payload={"groups": [group_id]}, method="PUT")
    print(f"Fleet-Bypass group (id={group_id}) set for {len(clients)} client entries "
          f"(assign zero adlists to Fleet-Bypass in the Adlists page — P2 covers deny "
          f"domain-list entries too, check those separately).", file=sys.stderr)


def setup_conditional_forwarding(base_url, sid, opnsense_lan_ip, lan_cidr="192.168.8.0/24"):
    # config.yaml: /api/config only defines get/patch (no post); config.c:606-611 pins
    # dns.revServers as a CONF_JSON_STRING_ARRAY of "<enabled>,<cidr>,<server>[,<domain>]"
    # strings, not an object. PATCH is also idempotent -- it's a plain config write, not
    # an insert -- so a re-run just restates the same value.
    rev_server = f"true,{lan_cidr},{opnsense_lan_ip}"
    _request(base_url, "/api/config", sid=sid, method="PATCH", payload={
        "config": {
            "dns": {
                "revServers": [rev_server]
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
