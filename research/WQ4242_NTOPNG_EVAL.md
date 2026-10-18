# wq#4242 — EVALUATE: ntopng network monitoring for post-OPNsense deployment

**VERDICT: DEFER — this question was already asked and answered.
`research/DIGEST_Fleet_searchability_md.md:78` (row R19) evaluated `os-ntopng` for
exactly this use case ("top-talker/anomaly visibility") and returned **DEFER: "Same
gate"**, pointing at R18/R10's shared blocker: there is no live firewall to run it on.
That gate has not moved since. No code changed; this is a research verdict plus the live
evidence behind it, re-confirming R19 rather than re-deriving it from scratch.**

## The gate R19 pointed at is still open

`research/NETWORK_PREMORTEM.md:5` states the current status of the OPNsense box in one
line: "**Status: PRE-DEPLOYMENT CHECKLIST for the Topton MW-JSL2.5G-4LV10 (OPNsense) and
the RPi 4B (Pi-hole)**... It is not a deployment. This ticket applied no rule, resolver
change or DNS setting to the live network."

`research/OPNSENSE_REWIRE_PREP.md:1-7` (wq#4011, independently CONFIRMed by
`reports/wq4049_verify_wq4011_opnsense_rewire_prep.md`) is more recent (2026-10) and still
describes prep work, not a live box: target LAN `192.168.8.1/24` vs. the current
factory-default `192.168.1.1` (`:15,32-38`), WAN cabling physically unresolved
(`:100-106`), and installed RAM unverified (`:39-45`) — all flagged as physical TODOs for
Frank, not blockers on anything else. Tailscale shows `opnsense-fw` **offline, last seen
9d ago** as of the wq#4011 session (`:105`).

`ops/wq3653-opnsense-tailscale-pihole-a1/README.md:1-9` adds the operational half of the
gate: OPNsense, the Pi-hole RPi, and the Flint 3 router are not in
`harness/fleet-exec-policy.yaml`'s SSH allow-list at all (confirmed here —
`grep -n "opnsense\|pihole\|flint" harness/fleet-exec-policy.yaml` matches nothing but
comment lines). No worker sandbox has a path to touch any of the three boxes today, which
means even a "just evaluate the plugin install" spike can't be run from here.

## Why the ntopng-specific gate is the same gate, not a new one

ntopng's two deployment modes both need the thing that's missing:

- **`os-ntopng` plugin (what the ticket asks for)** mirrors FreeBSD kernel traffic via
  `netmap-fwd`, which only exists once OPNsense is the box actually carrying fleet
  traffic. R10 (`DIGEST_Fleet_searchability_md.md:69`) already flagged the same
  dependency for Zenarmor DPI on this hardware: "Gated on hardware Frank owns but has not
  deployed." R18 (`:77`) flagged it again for `os-telegraf`: "Firewall metrics only
  become interesting once there is a firewall." ntopng is a third instance of the
  identical precondition.
- **Standalone ntopng + Redis**, ingesting NetFlow/sFlow instead of a kernel mirror,
  would need a flow exporter bolted onto the *current* router — the GL.iNet Flint 3 — but
  that box is explicitly slated for demotion once the OPNsense rewire lands
  (`research/OPNSENSE_REWIRE_PREP.md`'s flat-LAN-to-Topton migration). Building interim
  tooling against hardware already marked for replacement isn't worth the setup cost
  (GeoIP DB, SPAN/NetFlow config, a new container) for a window measured against an
  unscheduled physical rewire.

## What already covers the adjacent need

`research/FLEET_MONITORING_REDESIGN.md:79` shows Gatus's `0-backhaul` check (GL-BE9300
wwan, TP-Link gw, 1.1.1.1 ICMP, 8.8.8.8 DNS, `4/4 success=true`) — this confirms uplink
*reachability*, not traffic volume or DPI, so it doesn't substitute for ntopng's actual
value. No vnstat/iftop/NetFlow/sFlow/SNMP tooling exists anywhere in this repo (grepped
clean); VictoriaMetrics/Grafana on Bes (`research/WQ4238_LOKI_PROMTAIL_EVAL.md:9-33`,
confirmed live) ships no flow or bandwidth dashboard either. There is no redundant-tooling
risk here the way there was for wq#4238 (Loki) or wq#4240 (promptfoo) — the fleet
currently has zero traffic-pattern visibility, which is exactly the gap this ticket
describes. The blocker is access to the box, not a crowded field of alternatives.

## Recommendation

Do not build or stage anything for ntopng now — there is nothing to point it at, and no
sandbox can reach the box that would eventually run it. Re-open this ticket once
`reports/wq4049_verify_wq4011_opnsense_rewire_prep.md`'s successor confirms OPNsense is
actually carrying traffic (Tailscale shows `opnsense-fw` online, not `offline, last seen
9d ago`) **and** `harness/fleet-exec-policy.yaml` grants a sandbox SSH reach to it. At
that point the plugin path (`os-ntopng`) is the right one — standalone NetFlow against
the Flint 3 is not worth building as an interim step this close to its planned
decommission.
