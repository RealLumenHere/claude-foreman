# wq#3108 — fleet-standard INPUT default-drop (hathor / isis / wadjet / maat)

Class sweep wq#3089 (2026-09-28): hathor, isis, wadjet, maat run **symmetric ACCEPT** —
no host firewall on either stack. This directory defines the fleet standard and ships the
tooling to bring a node to it safely: recon → generate → review → apply-with-dead-man →
persist. Reference posture: `/home/lumen/scratch/wq-v6-default-drop/` (Lumen's v6 fix,
wq#3089) and `ops/wq2808-tailnet-raw-lockdown/TEST_PLAN.md` (rule-by-rule flow walk).

## The fleet standard

`filter INPUT` policy **DROP** on v4 **and** v6. Accepts, in order:

| # | rule | why |
|---|---|---|
| 1 | `-i lo -j ACCEPT` | loopback services (MCP gateways, DBs) |
| 2 | `-m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT` | replies; keeps the live ssh session up through the flip |
| 3 | `-i tailscale0 -j ACCEPT` (or keep the existing `ts-input` jump where tailscaled manages it) | fleet control plane rides the tailnet |
| 4 | `-i <LAN_IFACE> -s <LAN_SUBNET> -p tcp --dport 22 -j ACCEPT` | LAN ssh recovery path if tailscaled dies |
| 5 | v4: `-p icmp -j ACCEPT` · v6: `-p ipv6-icmp -j ACCEPT` | path health; v6 ND/RA is **mandatory** or the node loses its LAN v6 neighbors |
| 6 | v6: `-i <LAN_IFACE> -p udp --dport 546 -s fe80::/10 -j ACCEPT` | dhcpv6-client, only where recon shows a DHCPv6 lease |
| 7 | per-node extras | **only** from the recon listener report, each with a named consumer |

`FORWARD`/`OUTPUT` policies and all non-INPUT chains (Docker, ts-*) are preserved
verbatim — the staged file is generated **from the node's live save**, never from a bare
template, so a restore can't wipe Docker/tailscale chains.

## Why recon is a hard gate, not advice

The ticket requires designing from what each node actually listens on. `genrules.py`
**refuses to run without a recon bundle** and emits a listener-coverage report naming, for
every listening socket, which staged rule admits it and from where. Any wildcard listener
that ends up tailnet+LAN-unreachable is listed as CUT — the reviewer must either add a
justified extra accept to the node vars or accept the cut, before `apply.sh` will go.

## Per-node order and constraints

One node at a time, in this order, tailscale ssh path verified from Lumen **before** each:

1. **hathor** — plain worker. NOTE: has been bench/quarantine-cycled (see
   `agents/work-queue/reports/362_hathor_bench_runbook.md`); confirm it is actually up first.
2. **isis** — plain worker.
3. **maat** (vault **standby** first if it is standby at apply time) — login user `maam`,
   **PASSWORD sudo**: apply must be run by an operator session that can answer the prompt
   (Frank or an interactive session). Not headless-able today.
4. **wadjet** — vault pair, NOPASSWD sudo. Apply only after maat is confirmed healthy in HA.

Vault pair (wadjet/maat) extra rules:
- **Never touch `listen_addresses`** or any vault config — firewall only.
- Before the flip, recon must show which iface/subnet carries vault HA replication and the
  peer's traffic; that path gets an explicit accept in the node vars. Do not rely on it
  "probably" being tailscale0 — read the recon.
- Follow HA safe-switchover: apply on the standby, verify cluster health, only then the
  active. If anything looks off, the dead-man restores the old rules in ≤180 s anyway.
- Per instinct 5, launch `apply.sh arm` on the vault pair via detached
  `systemd-run --unit wq3108-apply-<node>` so an ssh drop cannot kill it mid-flight.

## Runbook (per node, from Lumen)

All commands below run **on the target node** (ssh from Lumen; every step is also safe to
wrap in `fleet-exec run <node> -- ...` once a grant covers it):

```
# 0. verify the tailscale ssh path you will keep using
ssh <node>-tailnet-addr true

# 1. recon (read-only), fetch the bundle back to Lumen for review
sudo bash recon.sh /tmp/wq3108-recon

# 2. generate staged rules + coverage report ON LUMEN from the fetched bundle
python3 genrules.py --recon <bundle-dir> --vars nodes/<node>.vars --out <staged-dir>
#    review: staged diff vs live must be exactly the standard lines + justified extras;
#    coverage report must show zero unexplained CUT rows.

# 3. copy staged dir to the node, then arm + apply (dead-man auto-restores in 180 s)
sudo bash apply.sh arm <staged-dir>

# 4. from Lumen, IMMEDIATELY verify: tailnet ssh still works, LAN ssh works,
#    node services still answer (vault: cluster health), THEN:
sudo bash apply.sh disarm <staged-dir>   # cancels rollback, persists rules
```

If step 4 verification fails: do nothing — the dead-man restores the backup at T+180 s.

Persistence: `disarm` writes `/etc/iptables/rules.v4` + `rules.v6` and re-saves via
`netfilter-persistent save` when present; otherwise it leaves the files and warns that a
boot-time restore unit still needs wiring on that node (record it in the node's follow-up).

## Status (2026-09-29, wq#3108)

Standard + tooling defined and tested here (`test/test_genrules.py`). **Not applied**: this
worker session had no fleet-exec grant (`fleet-exec: DENY [grant:grant_missing_env]`) and
no ssh on the Bash allowlist, so live `ss -ltnp` recon and per-node apply are queued as
follow-up tickets, one per node, in the order above.
