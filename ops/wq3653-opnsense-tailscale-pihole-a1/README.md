# wq#3653 — OPNsense integration: Tailscale + Pi-hole + exit node + subnet routing (STAGED, not applied)

**Nothing in this directory has touched the Topton (OPNsense), the RPi (Pi-hole), or the
Flint 3.** None of those three boxes is reachable from this worker: `harness/fleet-exec-policy.yaml:189`
grants read-only SSH to `[lumen, isis, bes, anubis, maat, sobek, hathor, ptah, sekhmet,
horus, neith, khnum]` only — the OPNsense box, the Pi-hole RPi and the Flint 3 are not on
that list, so there is no path from this sandbox to run a single command against them,
let alone configure them live. This directory is the runbook + scripts a session *with*
LAN/console access to those three boxes should execute, plus the test plan that proves
each step worked.

## Why this shape

`research/NETWORK_PREMORTEM.md` (wq#2483) already worked out the hazards for this exact
deployment — hard-NAT from default outbound NAT, the Pi-hole-sees-the-firewall's-IP
bypass-group trap, which ports actually cross the wire. This ticket turns that pre-mortem
into the actual step list the ticket asked for, grounded against current upstream docs
(`fleet-search web`, 2026-10-08: tailscale.com/docs/install/opnsense, docs.pi-hole.net/api/,
r/GlInet double-NAT threads — queries and results in the session transcript, not re-pasted
here per the output-discipline rule).

## Files

- `RUNBOOK.md` — all 9 ticket items, each step tagged `[SCRIPT]` (automatable from a
  console with access, script provided), `[GUI]` (OPNsense/Pi-hole/GL.iNet expose no
  stable CLI/API for this specific setting — verified against current docs, not guessed),
  or `[FRANK]` (Tailscale admin-console approval, which lives on Frank's tailnet account —
  the "mint a credential on his personal account" exception in Tier-0 instinct 3).
- `opnsense_tailscale_cli.sh` — the real `tailscale` CLI invocations for items 1, 2, 5, 7
  (first half), 8 (accept-dns), to run over SSH on the OPNsense console once the
  os-tailscale plugin has installed the real `tailscaled`/`tailscale` binaries. Not run
  from here — this sandbox cannot reach the box (see above).
- `pihole_fleet_bypass.py` — Pi-hole v6 REST API script for item 8 (DNS answer scope) and
  the §1.2 `Fleet-Bypass` group mechanism from `NETWORK_PREMORTEM.md:39-57`, plus item 9
  (conditional forwarding for `192.168.8.0/24` to OPNsense Unbound). Talks to `/api/auth`
  and the `/api/groups`, `/api/clients`, `/api/config` resources, shapes pinned against
  pi-hole/FTL's own source (wq#3707, after wq#3700 found the original web-UI-page-name
  guess wrong on four counts — see
  `foreman/reports/wq3700_judgment_wq3653_opnsense-tailscale-pihole-a1.md`). `/api/docs`
  itself is a RapiDoc UI, not a JSON spec, so the pre-flight can only confirm it's
  reachable and points the operator at `/api/docs/specs/*.yaml` for the real shapes — see
  `pihole_fleet_bypass.py:1-18`.
- `TEST_PLAN.md` — the acceptance probes, built on `NETWORK_PREMORTEM.md` §4's checklist,
  one probe per ticket item plus the P1-P4 Pi-hole bypass gotchas and the D1-D14 domain
  table.

## Decision record (full-autonomy call, Tier-0 instinct 3)

- Item 9, "Demote Flint 3 to AP mode OR disable masquerading": **chose AP mode** over a
  masquerading toggle. Disabling masquerading alone still leaves the Flint 3 doing its own
  DHCP/routing (double subnet), which collides with OPNsense owning
  `192.168.8.0/24` (ticket item 4). AP mode hands all of that to OPNsense cleanly, matches
  the standard GL.iNet-behind-a-real-router pattern surfaced by `fleet-search`
  (r/GlInet, "switch the ISP router to the ACCESS POINT mode"), and is the one the vendor
  UI exposes as a single toggle instead of hunting multiple firewall/DHCP settings to turn
  off piecemeal. Recorded here per instinct 3 ("decide and act... record the reasoning,
  tell him after the fact") rather than left as an open choice.
