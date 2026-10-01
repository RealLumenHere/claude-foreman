# wq#3334 bench card — confirm P1 Pro units, flash A (neith)

For Frank at the bench. Design wq#3261 Frank-physical items 1-2
(`agents/work-queue/reports/p1pro_role_assignment_design.md`). Everything a
machine can prep is already staged; this card is only the physical part.
Commands marked "from any box" run on your laptop/Thor; commands meant for
Lumen are SSH-wrapped — never type a bare `wq`/`nmap` line expecting it to
land on Lumen.

## 0. Confirm the three units (design phase 0)

| Label it | Hostname | Disk | Role |
|---|---|---|---|
| A | neith | 256GB | Commander backup + fleet-memory replica — **flash this one now** |
| B | ptah | 256GB | Commander primary (later, Frank-driven cutover) |
| C | khonsu | 512GB | Log archive (last) |

Note each unit's serial/model/RAM off the label (the design left RAM/NIC/disk
unverified on purpose — bring-up re-checks them). Only A is touched today.

## 1. Download the image (on whichever box runs Etcher)

- Armbian **firefly-rk3399**, "Resolute" build (Ubuntu 26.04 arm64), from the
  board page: armbian.com/boards/firefly-rk3399 (per the wq#2744 kit README).
- Verify the **sha256** the site publishes against the downloaded file before
  flashing anything.

## 2. Flash

- **Balena Etcher only, never dd** (fleet rule). Target: a microSD card —
  SD first, not eMMC, no maskrom tooling needed.

## 3. First-run config (before first boot)

Re-mount the SD card's boot partition after Etcher finishes. If
`armbian_first_run.txt.template` is present (field names drift across Armbian
releases — trust the template in the image you downloaded, not this card),
copy it to `armbian_first_run.txt` and set:

```
FR_general_delete_this_file_after_completion=1
FR_net_change_defaults=1
FR_net_ethernet_enabled=1
```

Wired LAN only — no WiFi fields.

## 4. Boot

Insert the SD into unit A, cable it to the 24-port core switch, power on.
It DHCPs onto 192.168.8.0/24.

## 5. Confirm it answers, then hand back to the fleet

From any box, find the lease (router admin page also works):

```
ssh lumen@100.117.66.102 'nmap -sn 192.168.8.0/24'
```

New 192.168.8.x that isn't in fleet-inv = neith. Then close this ticket —
that is the trigger: wq#3296 carries `Liveness: ticket_done(3334)` and its
runbook (`ops/wq3296-neith-bringup/README.md`, staged, PR #1544 landed)
replays from there:

```
ssh lumen@100.117.66.102 '/home/lumen/bin/wq done 3334 --notes "units confirmed, A flashed+booted, dhcp ip=192.168.8.X" --no-artifact "physical: bench work"'
```

Nothing after power-on is yours: the first-run wizard, provisioning,
hardening, Commander-backup install, and the memory replica are all wq#3296
(steps 1-6 of its README). The only other [FRANK] step there is the
`cloudflared tunnel login` browser auth, and it will say so when it's reached.
