# wq#4331 -- Desktop Commander MCP on Maksoor (RE workstation)

Direct RE-workstation access for agents: Desktop Commander MCP, reached over
Maksoor's own tailnet IP, so overnight RE sessions can run mtkclient, ADB,
rkdeveloptool, etc. (already installed on Maksoor per
`lumen_state.py current maksoor` -> `node_provisioning`) directly on the box
with the target device plugged in -- no SSH-hop through Lumen first.

## Why a standalone endpoint, not the Horus/mcpw path

Lumen's own `desktop-commander-mcp.service` was retired 2026-10-13
(`docs/fleet/retired_services.yaml`, wq#3805): Commander now runs centrally on
Horus (mcp-proxy), reached via the public `mcpw.lumennode.uk` gateway. That
retirement is about Lumen's *own* control-plane path -- it does not give
agents a way to reach Maksoor's local hardware, and routing through a public
gateway for same-tailnet, same-room hardware access would add a hop this
ticket exists to remove. This bundle is a standalone `dc-gateway.service` on
Maksoor itself, modeled on the same unit already shipped (but not yet live)
for Anubis and Bes (`ops/wq2607-dc-endpoint-anubis`, `ops/wq2608-dc-endpoint-bes-a1`)
and for the P1 pair (`ops/p1-commander`) -- minus the cloudflared/DNS/Access
and HA-watchdog pieces those carry, since Maksoor is a single bench laptop
reached over Tailscale only, not a public HA backend.

## What ships here

| File | Purpose |
|---|---|
| `dc-gateway.service` | Supergateway-wrapped Desktop Commander unit, `User=maksoor`, port 3100. MemoryHigh=2G/MemoryMax=3G sized for Maksoor's 8GB total RAM (`node_provisioning` fact) -- re-check with `free -h` after first start. |
| `dc-gateway-hardening.conf` | T1 cgroup hardening (CPUWeight/IOWeight/MemorySwapMax/Nice/OOMScoreAdjust), same shape as Anubis/Bes/Lumen's own drop-in, with Nice=0 (not Lumen's -5) because Frank sits at this machine interactively during RE sessions and the gateway must not out-prioritize his own tools. |
| `dc-gateway-logging.conf` | Routes supergateway's request/response body logging to a local 0600 file instead of journald (secrets-at-rest, wq#1698 parity) -- firmware dumps and device credentials can pass through those bodies. |
| `dc-enforce-config.py` | Maksoor-homed copy of the live tier-1 `blockedCommands` enforcer (`/home/lumen/bin/dc-enforce-config.py`), byte-identical BLOCKED/FORCE. None of `dd`/`mkfs`/`wipefs`/`shred`/`fdisk`/`sgdisk`/`parted`/`reboot`/`shutdown`/`poweroff`/`halt`/`macchanger` collide with mtkclient/adb/rkdeveloptool/edl/heimdall/etc., so the ticket's named tools run unrestricted; `dd` stays blocked per existing fleet USB policy (Balena Etcher only). |
| `install.sh` | Deploy-verify bundle per `ops/VERIFY_CONTRACT.md` (wq#1696). `--verify` runs either locally on maksoor or remotely via ssh (same dual-mode shape as `ops/wq3522-ser2net-boot-log-maksoor/install.sh`). The install path: Node check -> pinned npm globals -> enforcer copy -> units+drop-ins -> `daemon-reload` + `enable --now` -> `/health` check. |

## Deploy (next session with live ssh to maksoor)

```
scp -r ops/wq4331-dc-maksoor maksoor:/tmp/
ssh maksoor 'sudo bash /tmp/wq4331-dc-maksoor/install.sh'
```

## Status: NO-PROBE, shipped undeployed

This BUILD worker's Bash allowlist (`harness/worker-settings.json`) has no
`ssh`/`scp` entry at all -- confirmed by reading the allowlist directly, not
by trying and retrying a blocked command. This is the same wall wq#3151
(`deploy-ccc-on-maksoor`) hit and the same class as `kb/ISSUE-LOG.md` #740
(no exec grant to other fleet nodes from a sandboxed BUILD worker) and #1055b
(wq#3151 itself). `install.sh --verify` run from this worktree would report
`ERR unreachable: maksoor` via `vremote_up` -- per `ops/VERIFY_CONTRACT.md`,
an unreachable/ungranted node is `ERR`, never a `MISS`: "I could not look"
must not read as "it is not deployed."

maksoor's own availability fact (`lumen_state.py current maksoor` ->
`availability = available (since 2026-10-05) [verified 14d ago] [conf 0.25]`)
is stale and low-confidence besides -- consistent with maksoor's established
recurring-offline condition (`reports/wq3544_verify_wq3524_maksoor_ignore_live_deploy_gap.md`).

This bundle ships complete and ready to run. Needs: a session with an
`ssh(maksoor)` grant (the real `lumen` account, or Frank) to run the deploy
line above, then `lumen_state.py ingest maksoor dc_endpoint ...` to record it.

## Config facts, verified not guessed

- Desktop Commander's `blockedCommands` matching semantics (basename(argv0),
  per chained segment) and the live BLOCKED list: read directly from
  `/home/lumen/bin/dc-enforce-config.py` (in this session's `--add-dir`),
  not re-derived from memory.
- npm package names/versions (`supergateway@3.4.3`,
  `@wonderwhy-er/desktop-commander@0.2.46`): the same pins already used by
  the Anubis/Bes/P1 bundles, not a freshly guessed version.
- Maksoor's existing RE toolchain (mtkclient, adb, rkdeveloptool, edl,
  heimdall, sunxi-fel, openocd, etc.), tailnet IP (100.81.204.74), ssh user
  (maksoor), and total RAM (8GB): read from `lumen_state.py current maksoor`,
  not assumed.
