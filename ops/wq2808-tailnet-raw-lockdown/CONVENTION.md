# Fleet convention: tailnet-scoped port restrictions go in raw PREROUTING (wq#2808)

## The rule
A restriction that must apply to traffic arriving on `tailscale0` — "only Bes may reach
:11434", "nothing on the tailnet may reach :3100" — is written as a raw-table
PREROUTING rule and persisted with `netfilter-persistent save`. Never as a filter INPUT
rule, never as a ufw rule.

```
iptables -t raw -A PREROUTING -i tailscale0 -p tcp --dport <port> [! -s <allowed-peer>] \
    -m comment --comment "wq<N>-<service>-<port>-tailnet-<drop|restrict>" -j DROP
netfilter-persistent save
```

## Why (the class)
tailscaled owns the filter table's `ts-input` chain and keeps `-j ts-input` as rule 1 of
INPUT, and that chain ACCEPTs everything on `tailscale0` unconditionally. ACCEPT is
terminal, so any INPUT rule an operator writes below it never sees a tailnet packet —
it silently governs LAN and docker traffic only. Inserting our own rule ABOVE the jump
is not a fix either: tailscaled re-inserts its jump at position 1 whenever it reconciles
(restart, upgrade, netcheck), and the operator rule drifts back under it with no signal.
ufw is the same chain family, one jump later.

The raw table is evaluated before conntrack and before filter, and tailscaled does not
manage it, so a raw rule survives every reconcile and cannot be shadowed. Proven live
by wq#2769 (11434 restricted to Bes), staged for 3100 by wq#2808.

## What stays in filter INPUT
LAN- and docker-scoped allowances and drops (`-s 192.168.8.0/24`, `-s 172.16.0.0/12`,
`-i docker0`, `-i br-*`, `-i lo`) are correct where they are: those packets do not
enter through ts-input's accept. An existing INPUT per-port DROP is still live for LAN;
do not delete it when adding the raw rule, it is the other half of the same policy.

## Provisioning
`bootstrap/remote-provision.sh` grants `ufw allow in on tailscale0` on every node. That
is the fleet's trust default, and this convention is how a node narrows it for one
port. A provisioning script that needs a tailnet restriction uses the raw form above.

## Applying one on Lumen
Lumen is reached over the tailnet (SSH) and through cloudflared on lo (Commander). A
raw rule that names `--dport 22` or matches on `lo` can cut the operator off. Every
apply on Lumen: arm a detached `systemd-run --on-active` rollback FIRST, insert, run the
live checks from a peer (Bes), disarm only on a clean run, then persist. Template:
`foreman/ops/wq2808-tailnet-raw-lockdown/apply.sh`.
