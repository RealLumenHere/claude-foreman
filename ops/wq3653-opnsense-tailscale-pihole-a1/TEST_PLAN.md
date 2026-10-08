# wq#3653 — TEST_PLAN: acceptance probes, one per RUNBOOK.md item

Run every probe from a node that can reach the three boxes. Each probe is designed to be
able to fail (per `research/NETWORK_PREMORTEM.md` §4 and the fleet's AN-ACCEPTANCE-PROBE
doctrine): it checks the specific failure mode the step was meant to prevent, not just
"the command returned something."

| item | probe | pass | fail mode it catches |
|---|---|---|---|
| 1 | `pkg info os-tailscale` on OPNsense console | package listed, version shown | plugin silently not installed (pkg mirror issue, wrong repo) |
| 2 | `tailscale status` on OPNsense console | this node listed, not `Logged out` | auth key invalid/expired, `tailscale up` never completed |
| 3 | OPNsense GUI `Interfaces > Overview` | `TAILSCALE` interface present and up | assignment step skipped or reverted |
| 4 | from a tailnet peer, `curl` to an OPNsense LAN-side service through the tailscale0 path | connects | pass rule missing, default-deny on new interfaces blocks everything (OPNsense's default for a new interface is deny) |
| 5 | `tailscale netcheck` from an off-LAN fleet node | reports the 192.168.8.0/24 route reachable via opnsense-topton, not via the old router | route advertised but never approved (step 5's `[FRANK]` half) — this is the probe that catches a half-finished step 5, not just a crashed command |
| 6 | `tailscale netcheck` on OPNsense console, post-cutover | `UDP: true`; `tailscale status` shows peers as `direct`, not `relay` | hybrid NAT/static-port not applied — this is the exact "hard NAT" failure NETWORK_PREMORTEM §0.2 describes, and it still passes a naive "tailscale is up" check, which is why netcheck (not status) is the probe |
| 7 | from a phone/laptop on the tailnet (not on the LAN), set exit node to opnsense-topton, check public IP | public IP matches the Topton's WAN IP | exit node advertised but not approved, or outbound NAT for 100.64.0.0/10 missing (traffic leaves the tailnet fine but never reaches the internet) |
| 8a | `dig @<pihole-tailnet-ip> google.com` from a fleet static IP | NOERROR, real answer, not `0.0.0.0`/NXDOMAIN | Fleet-Bypass group missing this client, or client still in Default too (P1) |
| 8b | repeat 8a sourced from a tailnet IP (not LAN) | same clean answer | P3: client identified by the wrong address, tailnet CIDR not in Fleet-Bypass |
| 8c | every domain in `NETWORK_PREMORTEM.md` D1-D14 via `getent hosts <domain>` from a fleet node | all resolve | a deny-list entry assigned to all groups (P2), or Pi-hole sinkholing something the fleet actually needs |
| 8d | on OPNsense console, `tailscale status --json \| grep -i accept-dns` (or GUI VPN>Tailscale>Configuration) | `false` | accept-dns left true: OPNsense could start resolving through Pi-hole, which forwards fleet-range PTR/A queries back to OPNsense's own Unbound — the loop in NETWORK_PREMORTEM §0.4 |
| 9 | `dig -x 192.168.8.<some-lan-host> @<pihole-ip>` | PTR resolves to the real hostname (via OPNsense Unbound's DHCP lease names), not NXDOMAIN | conditional forwarding not set, or pointed at the wrong OPNsense interface IP |
| 10 | from a device connected to the Flint 3, `ip route` / check gateway | gateway is the OPNsense LAN IP, not the Flint 3's own address | Flint 3 still routing/NATing — double NAT, breaks item 4's assumption that fleet traffic reaches OPNsense's rules directly |

## Regression check (NETWORK_PREMORTEM §2.3 — only if the Topton segments VLANs)

If item 3's interface assignment put LAN hosts on a different broadcast domain than
before (a VLAN split, not just an edge-only replacement):

- `pg_is_in_recovery()` on Ma'at reports `t` (L1: Postgres streaming replication from
  Wadjet, `192.168.8.202` → `192.168.8.173`, still crosses).
- `plug-ctl all state` returns real states, not timeouts (L3: Tapo plug fleet).
- A Home Assistant API call from a fleet node succeeds (L2, `192.168.8.177:8123`).
- A WoL packet actually wakes a node on the other side (L4) — OPNsense needs its WoL relay
  service enabled, or this silently never worked.

## IDS/IPS (NETWORK_PREMORTEM §3, if Suricata/Zenarmor gets enabled on this segment)

Run `/home/lumen/bin/fleet-search web "test"` in a 5-minute burst and read the alert log
for fleet source IPs — zero **drops** is the pass condition (alerts are fine and expected;
a drop means IPS mode caught the scraping traffic fleet-search generates and killed it).
