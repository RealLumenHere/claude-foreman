# wq#2744 -- P1 Commander runbook

Prep only, per the ticket: nothing here has been installed, deployed, or run.
Boxes ("Station P1 Pro" x2, arm64) are not on hand yet ("arrives in a few
days"). This directory is everything needed to move fast once they do.

Working names used throughout this bundle: **p1-primary**, **p1-backup**
(fleet SSH-username-== hostname-lowercase convention). If Frank wants Egyptian
deity names instead, `sed -i 's/p1-primary/<name>/g; s/p1-backup/<name>/g'`
across every file in this directory BEFORE running install.sh -- nothing else
needs to change.

## What's in this directory

| File | Purpose |
|---|---|
| `ARM64_VERIFICATION.md` | Deliverable 1 -- arm64 research findings, incl. the one real gap found (Puppeteer/Chrome download inside desktop-commander's dependency tree) and its mitigation. |
| `install.sh` | Deliverable 2 -- root-run installer for ONE P1 node (--role primary/backup --hostname p1-primary/p1-backup). Installs Node, npm globals, dc-gateway unit + hardening + logging, the watchdog, cloudflared's --metrics drop-in. Prints the remaining manual steps it deliberately does NOT do unattended. |
| `dc-gateway.service`, `dc-gateway-hardening.conf`, `dc-gateway-logging.conf`, `dc-enforce-config.py` | The DC gateway unit + T1 hardening + secrets-at-rest logging + tier-1 command blocklist, adapted from Lumen's live unit (byte-matched, not guessed) and from wq#2607's Anubis staging. |
| `mcp-gateway-watchdog.sh` (+ .service, .timer) | Deliverable 2/4 -- the responsiveness watchdog (wq#2606/2745), byte-identical to the canonical fleet copy, wired here to probe mcp (local dc-gateway), cloudflared (local tunnel), and mcpw (the public front door, alert-only, never auto-restarts mcpw). This IS the "health = full MCP initialize" mechanism the ticket asks for. |
| `cloudflared-metrics.conf`, `supergateway-logrotate.conf` | Supporting drop-ins the watchdog and log hygiene need. |
| `cloudflared-ingress-p1-primary.yml`, `cloudflared-ingress-p1-backup.yml` | Deliverable 2 -- new tunnel ingress stanzas, new hostnames (mcp-p1-primary.lumennode.uk, mcp-p1-backup.lumennode.uk), never the live mcp-backend.lumennode.uk. |
| `p1-update.sh` | Deliverable 3 -- manual, Frank-driven update-and-record script for ONE P1 node. Never a timer (control-plane rule 3). |
| `p1-update-gate.sh` (+ .service, .timer) | Deliverable 3 -- the backup's "2 weeks behind, own window, sole machine touched then" policy as code: read-only, alert-only, runs ON LUMEN, opens an aq row + ntfy when due. |
| `mcpw-diag.py` | A durable replacement for the lost /tmp/mcpw_diag.py -- register->authorize->token->initialize. |
| `mcpw-cutover-plan.md` | Deliverable 4 -- the actual Worker MCP_BACKENDS flip, CF Access setup, verification, and rollback. Documentation only. |
| `p1-ha-drill.sh` | Deliverable 4 -- the wq#2781 drill script, run live with Frank watching, one step at a time. |
| `fleet_tools_p1.diff` | The context/fleet_tools.yaml entry for the new nodes, staged like wq#2607's Anubis entry. |
| `CHECKLIST.md` | The linear, execute-on-arrival version of everything below. |

## OS bring-up (before install.sh even applies)

The Station P1 Pro is a Rockchip RK3399 board, not a generic x86 PC -- it
needs a board-specific image, not a stock Ubuntu Server ISO. See
ARM64_VERIFICATION.md's OS note for why.

1. Download the Armbian **firefly-rk3399** board image, **"Resolute"** build
   (Ubuntu 26.04 arm64 -- matches the fleet's other nodes) from
   armbian.com/boards/firefly-rk3399. Verify the sha256 the site publishes
   against the downloaded file before flashing anything.
2. **Flash to a microSD card with Balena Etcher** (fleet rule: Etcher only,
   never dd -- applies here exactly as it does to x86 fleet nodes, just onto
   a different image format). SD card first, not eMMC directly -- simplest
   path, no maskrom/recovery-mode tooling needed.
3. Before first boot, mount the SD card's boot partition on any Linux box and
   check whether armbian_first_run.txt.template is present (field names have
   drifted across Armbian releases -- read the actual template in the actual
   downloaded image rather than trusting a fixed field list). If present:
   copy it to armbian_first_run.txt, set
   FR_general_delete_this_file_after_completion=1, FR_net_change_defaults=1,
   FR_net_ethernet_enabled=1 (wired LAN like every other fleet node, no WiFi
   fields needed).
4. Boot the board on Ethernet (DHCP by default). Find its DHCP lease on the
   router or via nmap -sn 192.168.8.0/24 / arp-scan -- same technique as the
   Mac-Pro bring-up gotcha already in claude_memory.md for boxes that come up
   on an unpredictable IP.
5. SSH in as root (Armbian's documented default first-boot credential --
   verify against the actual downloaded image's docs, these defaults
   occasionally change). Complete the one-time interactive wizard over that
   SSH session: set a new root password, then create the fleet-standard user
   -- username = the node's hostname (p1-primary or p1-backup), password
   the first-boot credential from Vault item p1-first-boot (fleet standard; never write it in this repo) -- with sudo.
6. From here, the standard fleet path applies unmodified:
   /home/lumen/bin/fleet-provision-ssh p1-primary <user>@<dhcp-ip> --password "$P1_FIRST_BOOT_PW" --ts-key tskey-auth-...
   (Path A of the provisioning skill -- OS+SSH already exist, now true after
   step 5). Tailscale join, Lumen key install, ssh-import-id gh:FrankStupak,
   key-login-verified-before-hardening. Repeat for p1-backup.
7. **Static LAN IP via netplan** (fleet standard, no node stays on DHCP) --
   pick two unused 192.168.8.x addresses.
8. Confirm RAM: `free -h`. The MemoryHigh/Max values in dc-gateway.service
   are placeholders sized for a 4GB board (see that file's header) -- Frank's
   actual order spec is unverified as of this writing; re-check and adjust
   before first start if the real number is materially different.
9. `lumen_state.py ingest p1-primary <hostname/hardware/lan_ip/tailscale_ip>`
   (mirror the pattern every other node's entry follows), then
   `state-regen`. Same for p1-backup.

## Then, per node: install.sh

```
scp -r foreman/ops/p1-commander p1-primary@<ts-ip>:/home/p1-primary/
ssh p1-primary@<ts-ip> 'sudo /home/p1-primary/p1-commander/install.sh --role primary --hostname p1-primary'
```
Repeat for p1-backup (--role backup). Then work through install.sh's own
printed "REMAINING STEPS" (cloudflared, DNS+Access, SSH keys, mcpw token,
state ingest) on each box -- see CHECKLIST.md for the exact sequence.

## Then: prove each node standalone, THEN cut over, THEN drill

In this order, not interleaved:
1. Both boxes pass `mcpw-diag.py --base-url https://mcp-p1-<role>.lumennode.uk`
   on their own tunnel hostnames, watchdog timer green for at least a day.
2. mcpw-cutover-plan.md -- the actual MCP_BACKENDS flip, Frank driving, with
   rollback ready.
3. Only once the cutover has soaked and Frank accepts it: p1-ha-drill.sh
   (wq#2781), live, Frank watching.
4. Only after the drill passes: retire Lumen's own desktop-commander-mcp
   (mcpw-cutover-plan.md section 5) -- this is the actual "Lumen leaves the
   control-plane path" moment, done last and deliberately.

## Known limitations / follow-ups (stated, not hidden)

- **mcpw watchdog token has no auto-renewal.** mcp-gateway-watchdog.sh --mode
  mcpw reads a static bearer token from a file; access-token TTL is 7 days
  (index.ts's accessTokenTTL: 604800), refresh-token TTL 30 days. Nobody
  re-mints it automatically. Re-run mcpw-diag.py --save-token before it
  expires, or the mcpw-mode probe alerts "failed" for a reason that isn't
  actually mcpw being down. Worth a follow-up ticket once the P1 pair is
  live.
- **Puppeteer/PDF gap** -- see ARM64_VERIFICATION.md. Mitigated (skip the
  broken download), not fixed (no arm64 PDF rendering path installed).
- **RAM-sized cgroup limits are placeholders** until the real boxes are in
  hand and `free -h` is run -- see dc-gateway.service's header.
- **p1-update-gate.sh's date parsing is untested against a real
  lumen_state.py fact** (no p1-primary/p1-backup subjects exist yet) -- the
  format it greps for matches every other subject's output verified this
  session, but run it once by hand against a real last_update fact before
  trusting the timer unattended.
- This plan does not resurrect the 2026-09-24 Anubis/Bes 3-4 node HA design
  (wq#2607/2608, staged but never deployed) -- wq#2744 supersedes it with a
  2-node P1 pair, Lumen fully out. If Frank wants BOTH, that's a new,
  explicit decision -- the router code supports N backends either way.
