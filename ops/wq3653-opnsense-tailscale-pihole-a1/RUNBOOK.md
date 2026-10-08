# wq#3653 — RUNBOOK: OPNsense + Tailscale + Pi-hole + exit node + subnet routing

Run this from a console/SSH session that actually reaches the Topton (OPNsense), the RPi
(Pi-hole) and the Flint 3 — none of that is true of this build worker (see `README.md`).
Do the §4 checklist from `research/NETWORK_PREMORTEM.md` before cutover and again after.
Keep the old router cabled for rollback until every TEST_PLAN.md probe passes.

Tags: `[SCRIPT]` — script in this directory, run on-box. `[GUI]` — no stable CLI/API,
confirmed by current docs, use the web UI. `[FRANK]` — needs Frank's Tailscale account.

## 1. Install os-tailscale plugin `[SCRIPT]`

On the OPNsense console:
```
pkg install os-tailscale
```
(OPNsense plugins are FreeBSD pkg packages served from the OPNsense repo; this is the
same mechanism `System > Firmware > Plugins` uses, just without the GUI click.) This
installs the real `tailscaled`/`tailscale` binaries plus the GUI's `VPN > Tailscale` page.
Confirm: `tailscale version` exits 0.

## 2. Authenticate with auth key `[FRANK]` to mint, `[SCRIPT]` to apply

The auth key itself is minted in the Tailscale admin console
(login.tailscale.com/admin/settings/keys) under Frank's account — that is the "mint a
credential on his personal account" exception, not something this or any worker session
can self-serve. Once Frank has produced a key (reusable, tagged, with an expiry — do not
request one that doesn't expire), apply it:
```
tailscale up --authkey=<KEY> --hostname=opnsense-topton --accept-dns=false
```
`--accept-dns=false` here is item 7's loop-prevention step, set at the same time so the
box never spends even one boot cycle pointed at a DNS server it hasn't configured yet.
See `opnsense_tailscale_cli.sh:1-12`.

## 3. Assign tailscale0 interface `[GUI]`

`Interfaces > Assignments` → add the `tailscale0` interface that appears once step 2 has
run. OPNsense's current stable API has no endpoint for interface assignment (confirmed:
the `/api/interfaces/*` tree covers overview/VLAN/bridge, not device assignment) — this
is a GUI-only step. Name it something recognisable (`TAILSCALE`) since later steps
reference it by name, not `tailscale0`.

## 4. Firewall pass rule on the Tailscale interface `[GUI]`

`Firewall > Rules > TAILSCALE` → add: pass, IPv4+IPv6, any protocol, source any, destination
any. (OPNsense's rule API (`/api/firewall/filter/*`) exists in current releases, but hand
authoring a rule via API without being able to read this box's actual rule ordering and
existing aliases first would be guessing at a live firewall — doctrine says verify, not
guess, and the only way to verify here is the GUI, which already shows rule order.) Save
and apply.

## 5. Advertise `192.168.8.0/24` as subnet route `[SCRIPT]` + `[FRANK]` to approve

```
tailscale up --advertise-routes=192.168.8.0/24 --accept-dns=false
```
(repeats the flags already set in step 2's `up` call — `tailscale up` takes the full
desired state each time, it does not merge). Then in the Tailscale admin console
(admin/machines), approve the route for `opnsense-topton` — route approval is
console-only by design (Tailscale's ACL/route-approval model), so this is Frank's step,
queued as a physical task, not a gate blocking the rest of the runbook.

## 6. Hybrid outbound NAT with static port for UDP `[GUI]`

`Firewall > NAT > Outbound` → mode: **Hybrid outbound NAT rule generation**. Add a rule
above the generated ones: interface WAN, source = the fleet's LAN subnet
(`192.168.8.0/24`), **Static Port: checked**. Also enable NAT-PMP (`Services > NAT-PMP` or
the equivalent current-release path) per `NETWORK_PREMORTEM.md` T3 — without this,
off-LAN Tailscale peers fall back to DERP relay instead of direct, which still works but
is slower and easy to miss. Confirmed against tailscale.com/docs/install/opnsense
(2026-10-08 fleet-search): "Select Hybrid Outbound NAT rule generation ... Check Static
Port ... Allow NAT-PMP."

## 7. Exit node `[SCRIPT]` to advertise, `[FRANK]` to approve, `[GUI]` for NAT

```
tailscale up --advertise-exit-node --advertise-routes=192.168.8.0/24 --accept-dns=false
```
Approve the exit node for `opnsense-topton` in the admin console (same Frank-gated step as
item 5 — batch both approvals in one admin-console visit). Then add a second outbound NAT
rule (step 6's page): interface WAN, source `100.64.0.0/10` (the Tailscale CGNAT range),
static port checked — this is what lets exit-node traffic from other tailnet devices
actually reach the internet through this box's WAN.

## 8. Pi-hole DNS wiring `[SCRIPT]` (Pi-hole side) + `[FRANK]` (tailnet DNS tab)

- On Pi-hole: permit all origins and disable local blocking for the fleet range, via the
  `Fleet-Bypass` group mechanism (`NETWORK_PREMORTEM.md:39-57`) — see
  `pihole_fleet_bypass.py`. Add both `100.64.0.0/10` and `fd7a:115c:a1e0::/48` as clients
  in `Fleet-Bypass` (P3: Tailscale-sourced queries arrive from the tailnet IP, not the LAN
  static IP) alongside the LAN addresses.
- In the Tailscale admin console DNS tab: add the Pi-hole's tailnet IP as a **Global
  nameserver**, and enable **Override local DNS**. This tab lives in the admin console,
  same as route/exit-node approval — `[FRANK]`.
- Back on OPNsense: `tailscale set --accept-dns=false` (already folded into steps 2/5/7's
  `up` calls, but stated as its own check here because it is the loop-prevention step —
  §0.4 of `NETWORK_PREMORTEM.md`: if OPNsense's own Unbound forwards to Pi-hole, and
  OPNsense's Tailscale client *also* accepted Pi-hole as its resolver via MagicDNS, the
  box would be asking Pi-hole to resolve the question it's about to ask Pi-hole, with no
  escape path and the full tailnet depending on the answer. `--accept-dns=false` keeps
  OPNsense using its own Unbound as the resolver regardless of what the tailnet DNS tab
  says).

## 9. Conditional forwarding in Pi-hole for `192.168.8.0/24` → OPNsense Unbound `[SCRIPT]`

Pi-hole's conditional forwarding is a dnsmasq `server=/<reverse-zone>/<ip>` directive
under the hood (`/etc/dnsmasq.d/01-pihole.conf` once set), exposed in v6 via
`/api/config/dns/revServers`. `pihole_fleet_bypass.py --conditional-forwarding` sets:
remote IP = the OPNsense LAN address (the Unbound resolver listens there), domain =
`192.168.8.0/24`'s reverse zone, local gateway IP = same OPNsense LAN address. This makes
PTR lookups for fleet LAN hosts resolve to real hostnames (OPNsense's Unbound knows the
DHCP lease names) instead of Pi-hole guessing.

## 10. Flint 3 → AP mode `[GUI]` (decision: AP mode, not masquerade-only — see README.md)

GL.iNet web UI → **Internet** or **More Settings > AP Mode** (exact menu path varies by
GL.iNet firmware version; the Flint 3 is not in this worker's SSH allowlist so the menu
could not be confirmed live — verify the exact label on the box before clicking). Switching
to AP mode hands DHCP/routing entirely to OPNsense and removes the double-NAT hop, which
is what items 4-7 assume (a flat path from fleet hosts to the OPNsense WAN rules, not a
second NAT layer in between). After the switch, confirm the Flint 3's WAN-side DHCP client
picks up an address from OPNsense's `192.168.8.0/24`, same as any other LAN device.
