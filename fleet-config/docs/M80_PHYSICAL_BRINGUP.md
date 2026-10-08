# M80 physical bring-up — MEBx/AMT + OS install (wq#3703, step 1 + 3a of wq#3652)

Lumen is headless and has no remote KVM onto this node yet — that's the
chicken-and-egg wq#3652 split on: AMT is what *gives* you a remote KVM
(MeshCommander, wq#3701), but configuring AMT requires a physical
keyboard/monitor on the M80 first. Nothing below can run over SSH or
fleet-exec; it is a checklist for whoever is physically at the machine
(full-autonomy instinct 3 — queued as a physical task, not a gate on the
rest of wq#3652's provisioning chain). Once this checklist is done, the
node continues through [RUNBOOK.md](RUNBOOK.md) from step 2 (Tailscale join)
like any other fleet node.

## Step 1 — Intel MEBx / vPro AMT

1. Power on (or reboot) the M80. During POST, press **Ctrl+P** when
   prompted to enter the Intel MEBx (Management Engine BIOS Extension)
   setup screen — this is separate from the regular BIOS/UEFI setup key.
2. Default MEBx login is the standard Intel AMT factory credential
   (username `admin`); MEBx forces a password change on first login and
   rejects anything that isn't 8+ chars with upper, lower, digit, and
   symbol. Pick a new password at the keyboard and record it directly in
   Lumen's credential store per `docs/RECOVERY.md` §6 conventions for this
   repo — do not write the password into this repo or any ticket/PR text
   (instinct 4; `fleet-config/README.md` "No credentials in this repo").
3. In the MEBx main menu: **Intel(R) AMT Configuration** →
   **Manageability Feature Selection** → set to **Enabled** (this is the
   "enable network access" step — AMT's out-of-band network stack is off
   by default).
4. Still under Intel AMT Configuration: **Network Setup** → **TCP/IP
   Settings** → **Network Interface** → confirm enabled, then
   **IPv4 Configuration** → **DHCP Mode** → **Enabled**. Do not set a
   static IP here; the fleet's IP source of truth is
   `lumen_state.py current <node>` via Tailscale, not a static AMT lease
   (`fleet-config/README.md` "node IPs" row).
5. Save and exit MEBx (Esc → Yes). The node reboots.
6. Record the AMT interface's MAC address (shown in the MEBx network
   screen) somewhere retrievable — wq#3701 (MeshCommander) and wq#3702/
   wq#3705 (fleet-exec-policy onboarding) need it to find the node on the
   LAN before it has a Tailscale IP.

## Step 3a — Ubuntu install on nvme0n1

**Disk layout is load-bearing — do not improvise it.** wq#3652 step 3b
reserves the *second* NVMe (`nvme1n1`) to be formatted later, as a
privileged post-install step, as `/var/fworker` (the fworker agent's
sandboxed workspace — see
`ops/wq3652-m80s-provisioning/README.md` in the wq#3652 branch and
`provision-fworker.sh` there). If the installer touches `nvme1n1` there is
nothing left for that step to format, and the fworker-sandbox hardening
(`fworker-sandbox@.service`) has no disk to isolate onto.

1. Boot the Ubuntu installer (USB), current LTS ISO.
2. At the disk step, pick **manual/custom partitioning** (not "erase disk"
   — "erase disk" on this installer targets whichever disk it defaults to,
   which is not guaranteed to be `nvme0n1` on a two-NVMe box). Confirm the
   target disk's identifier is `nvme0n1` (check by size/serial against
   `nvme1n1` if the installer shows both — most install media exposes this
   via a "show disk info" or the partitioner's device list).
3. Install the full root filesystem on `nvme0n1` only. Leave `nvme1n1`
   completely untouched — no partition table, no mount point, nothing.
4. Create the `lumen` user (matches every other fleet node's
   `ansible_user: lumen` in `ansible/inventory/hosts.yml`), with sudo.
5. Enable OpenSSH server during install (RUNBOOK.md step 1 prerequisite:
   "SSH server up; lumen's pubkey in the new node's `authorized_keys`") so
   the node is reachable the moment it's on the network — this is what
   turns the rest of wq#3652's chain back into remote, SSH-driven work.
6. After first boot, confirm both disks are visible and distinct before
   handing off: `lsblk` should show `nvme0n1` with the new root filesystem
   and `nvme1n1` with no partitions.

## Hand-off

This node has no assigned fleet hostname yet — one is not guessed here
(same reasoning wq#3652's README gives for leaving
`harness/fleet-exec-policy.yaml` unedited: guessing a name/IP this repo's
own doctrine forbids). Once Tailscale is up (RUNBOOK.md step 2) and
`lumen_state.py current <node>` resolves it, continue:

- wq#3701 — MeshCommander on Thor, using the MAC from step 1.6 above.
- wq#3702 / wq#3705 — `provision-fworker.sh`, fworker-sandbox systemd unit,
  Claude auth migration, and `fleet-exec-policy.yaml` onboarding.
