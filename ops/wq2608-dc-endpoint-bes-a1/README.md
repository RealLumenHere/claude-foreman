# wq#2608 — DC endpoint on Bes: dc-gateway + ingress on Bes's existing tunnel + Access app

Implements `/home/lumen/instructions/drafts/MCP_CONTROL_PLANE_HA_DESIGN.md` §3.2 for
Bes (N100, 16G, always-on HA/monitoring node). Same recipe as the sibling Anubis
ticket (wq#2607), with the one difference the ticket calls out: Bes already runs a
locally-managed cloudflared tunnel serving other hostnames, so this ADDS an ingress
rule rather than creating a new tunnel.

## What's in this directory

- `dc-gateway.service` — the Bes unit: `supergateway 3.4.3` fronting
  `@wonderwhy-er/desktop-commander 0.2.46` on `:3100`, stateless, same shape as
  Lumen's `desktop-commander-mcp.service` (verified via `systemctl cat`
  2026-09-25) but `User=bes`/`/home/bes/...` paths, **T1 hardening baked in
  directly** (`CPUWeight=1000 IOWeight=1000 Nice=-5 OOMScoreAdjust=-900
  MemorySwapMax=1G`, matching what wq#2605 made persistent for Lumen), and the
  ticket's memory note applied: `MemoryHigh=3G MemoryMax=4G` — far below
  Lumen's `20G/26G` because Bes is a shared 16G box (HA monitor, mosquitto,
  Grafana all live there too), not a dedicated control-plane node.
- `cloudflared_ingress_patch.py` — adds the `mcp-bes.lumennode.uk ->
  http://localhost:3100` rule to Bes's **existing** `config.yml`, inserted
  immediately before the catch-all rule (cloudflared requires the catch-all
  last; every other existing hostname is left untouched). Backs up the
  original to `config.yml.pre-wq2608.bak` the first time only. Idempotent —
  a second run against an already-patched config is a no-op. Tested against a
  synthetic 3-hostname config in `/tmp/fworker-wq2608/test/`: inserted
  correctly before the catch-all, existing hostnames unchanged, re-run
  produced a clean `NOOP`.
- `apply.sh` / `rollback.sh` — root-run installer/uninstaller for Bes:
  installs node LTS check, pins supergateway/DC, generates a Bes-homed copy
  of `dc-enforce-config.py` (see below), installs the unit, runs the ingress
  patch, `cloudflared tunnel ingress validate`, daemon-reload, enable+start,
  health-checks `:3100/health`, then detached-restarts `cloudflared`
  (quiet-window guarded — Bes's tunnel carries other live hostnames).
  `rollback.sh`'s two restore blocks are exercised without root or systemd by
  `test_rollback_restore_branches.py` (`python3 test_rollback_restore_branches.py`);
  its `DC_UNIT` block shipped with its `if`/`else` bodies swapped and was fixed
  under wq#2671 (ISSUE-LOG #766). The `DC_UNIT` and `CFD_CONFIG` blocks are
  deliberately *not* symmetric — don't "tidy" them to match; see the comment at
  the `DC_UNIT` block and the test's docstring for why.
- `dns_access_setup.sh` — DNS CNAME + CF Access app, split into its two
  *different* credentials (see below).
- `ssh_fleet_keys.sh` — generates an ed25519 identity for the `bes` user and
  prints the exact fleet-wide trust steps (§3.2's "ssh keys to fleet").
- `lumen_state_ingest.sh` — prints the `lumen_state.py ingest
  bes.mcp_endpoint …` + `state-regen` + `fleet_tools.yaml` commands.

## dc-enforce-config.py: one canonical source, node-local copies

`/home/lumen/bin/dc-enforce-config.py` hardcodes `CFG =
Path("/home/lumen/.claude-server-commander/config.json")` — that's
Lumen-specific. `apply.sh` generates `/home/bes/bin/dc-enforce-config.py` by
`sed`-ing `/home/lumen/` → `/home/bes/` over the canonical file at deploy
time, so the `blockedCommands` list (Tier-1 hardening, wq#1585) can never
drift between nodes — there is exactly one list to edit, on Lumen, ever.

## Why this ships as a proposal, not a live install

Two independent blockers, both structural to this BUILD worker's sandbox, not
this ticket specifically:

1. **No path to Bes at all from this session.** `harness/fleet-exec-policy.yaml`
   already allows `ssh(bes)` (read-only `run`) and `copy` into
   `/home/bes/lumen-drop` **at the policy layer** (`fleet-exec-policy.yaml:174,227`)
   — but the broker requires a matching PER-TICKET grant too (`needs:` tag,
   fail-closed unless both agree), and this ticket's capability lint only
   granted `dir(/home/lumen/instructions/drafts)`. Confirmed live:
   `fleet-exec run bes hostname` → `DENY [grant:grant_missing_env]: grant file
   did not verify`. Even with that grant, `fleet-exec` has no root/sudo verb
   for `bes` (`allow.restart` doesn't list it, `never.power` explicitly
   excludes it) — so a full apply still needs a root session on Bes, same
   carve-out as wq#2605/wq#2606/wq#1698. The concrete, actionable gap for a
   follow-up: request `ssh(bes)` + `copy(bes)` on the next ticket so the
   real `config.yml` can at least be **read** and diffed against what this
   bundle assumes, before a root session applies it.
2. **DNS/Access need wq#2614.** The CF API token
   (`/home/lumen/.secrets/cf_token`) was shredded 06-29 and its replacement is
   a Frank-only physical act (minting into his own CF account) — `wq show
   2614` is `frank_gated`, not done, as of this ticket. `dns_access_setup.sh`
   separates the two credentials this actually needs: the DNS route
   (`cloudflared tunnel route dns`) rides the tunnel's own origin cert and
   may not need the token at all; only the Access-app creation genuinely
   does.

## Verified vs. assumed

Verified live this session (read-only, no Bes access needed):
- Lumen's `desktop-commander-mcp.service` unit shape (`systemctl cat`) — used
  as the template.
- `dc-enforce-config.py` contents and its hardcoded `/home/lumen/` path.
- `fleet-exec-policy.yaml`'s `bes` entries (ssh allow, copy_dirs, power/restart
  deny).
- `fleet-exec run bes hostname` → denied for the reason stated above.
- `cloudflared_ingress_patch.py` against a synthetic config (this directory
  doesn't have Bes's real one).

Assumed from the design doc / ticket text, NOT independently verified this
session (no access to check): Bes's actual `config.yml` contents, its tunnel
UUID, and that its cloudflared login cert has `Zone:DNS:Edit` for
`lumennode.uk`. `apply.sh` and `dns_access_setup.sh` both read these live from
the box at run time rather than hardcoding a guess.

## To actually apply (needs root on Bes + wq#2614 for the Access half)

```
sudo ops/wq2608-dc-endpoint-bes-a1/apply.sh
# then, in order:
sudo /home/lumen/foreman/ops/wq2606-mcp-gateway-watchdog-a1/apply.sh   # WQ2606_GATEWAY_UNIT=dc-gateway WQ2606_GATEWAY_PORT=3100
ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh
ops/wq2608-dc-endpoint-bes-a1/ssh_fleet_keys.sh
ops/wq2608-dc-endpoint-bes-a1/lumen_state_ingest.sh   # after DNS/Access are live, not before
```

Rollback: `sudo ops/wq2608-dc-endpoint-bes-a1/rollback.sh`.
