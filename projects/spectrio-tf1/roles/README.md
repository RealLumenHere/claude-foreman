# wq#4181 — TF1 appliance role map + Armbian hardening pack

Adds 3 single-purpose roles to the TF1 ophub Armbian S905X3 fleet, on top of
the shared hardening pack below. These are new roles alongside — not a
replacement for — wq#3430's 5-role plan
(`ops/wq3430-spectrio-tf1-deploy/roles.yaml`: pathfinder-spare, klipper-host,
adguard-primary/secondary, kiosk-dashboard). See that bundle's README for the
wq#3429 flash procedure all of these share.

`roles.yaml` holds the role map; `render_role.py` validates + renders it;
`test_render_role.py` (16 cases: `python3 -m pytest test_render_role.py`)
covers all 3 roles, `bash -n` syntax-checks every shipped script, and
greps every script/config for the specific line implementing each ticket
requirement, plus negative cases (powersave governor on an exit node,
allow-all ACL on the vantage box, non-tailnet ADB host).

## Layering

Every role runs `harden-armbian.sh` first, then its own role script:

- `harden-armbian.sh` — shared hardening, applies to tf1-a/b/c alike:
  multi-user.target only, HDMI off (`video=HDMI-A-1:d`), USB autosuspend via
  udev, `rfkill block wifi` + `systemd-rfkill` for persistence,
  `armbian-ramlog` + volatile journald (keeps write-heavy logs off eMMC),
  `noatime,commit=600` on every ext4 fstab line, and the meson hardware
  watchdog (`meson_gxbb_wdt`, `RuntimeWatchdogSec=30`).
- `tf1-a-exit-node.sh` — `ip_forward` sysctl, a networkd-dispatcher hook that
  re-applies UDP GRO forwarding on every link-up (ethtool settings don't
  survive one), and a `performance` cpufreq governor unit. `tailscale up
  --advertise-exit-node --advertise-routes=192.168.8.0/24` is a one-time
  interactive command (needs the tailnet admin console's human approval
  afterward), not something this script runs for you.
- `tf1-b-external-vantage/` — Gatus (`ghcr.io/twin/gatus:v5-arm64`, sqlite on
  a container tmpfs, never the eMMC) plus self-hosted ntfy 2.28, both bound
  to the box's tailscale IP only, ntfy shipping `auth-default-access:
  deny-all`. This is the external-vantage leg: it probes Lumen from outside
  Lumen's own network and alerts over a path that doesn't depend on Lumen
  being reachable — closing the WAN-side Gatus blind spot aq#98/99/138/139
  flagged.
- `tf1-c-adb-host.sh` — `adb -a -P 5037 nodaemon server` under systemd, with
  an iptables rule restricting :5037 to the `tailscale0` interface so `-a`
  (bind all interfaces) doesn't also mean LAN-wide exposure.

## What ships in this ticket

Config and scripts only — same posture as wq#3430: no fleet-exec grant for
TF1 units from this worktree, so nothing here has been applied to real
hardware. `test_render_role.py` proves the schema, the shell syntax, and
that every file contains the line implementing its ticket requirement; it
cannot and does not claim a role boots.

### Blocked on bench access (physical_tasks, per role in `roles.yaml`)

Per the ticket's own "VERIFY FIRST on real TF1" instruction, each role's
`roles.yaml` entry carries the exact verification steps as `physical_tasks`
rather than a blocking gate (nobody but someone on-site can run them):

- **NIC speed** (100M vs 1G) — caps tf1-a's exit-node throughput regardless
  of CPU/GRO tuning; affects tf1-b's probe-latency readings.
- **RAM** — sizes tailscaled's netmap cache on tf1-a and flags whether tf1-b
  (Gatus+ntfy, both small) has enough headroom.
- **PoE PD power budget** — confirms the PD can sustain load, not just idle
  draw, at the estimated ~3 W/box (~$0.70/mo at typical US residential
  rates; `roles.yaml: est_power_draw_w` / `est_cost_per_month_usd`).
- **tf1-a**: the LAN subnet to advertise is assumed `192.168.8.0/24` per the
  ticket — confirm against the real TF1 network before running `tailscale
  up --advertise-routes`.
- **tf1-b**: ntfy's `auth-file`/ACL credential and Gatus's real WAN-facing
  endpoint list are both bench-time fill-ins (`gatus-config.yaml` and
  `ntfy-server.yml` ship with `PLACEHOLDER` values intentionally — a real
  credential or endpoint shouldn't be guessed into a committed file).
- **tf1-c**: confirm which Android devices this host services before wiring
  USB in.

None of the above block landing this plan — they're the fill-in-the-blanks
steps for whoever has the hardware in hand next, same pattern as wq#3430.

## Deps

- wq#3429 (flash procedure + census tooling,
  `/home/lumen/projects/spectrio-tf1/census/CENSUS_METHODOLOGY.md`).
- The eMMC zero-touch install path (`spectrio_zero_touch_and_emmc.md`) is
  where `harden-armbian.sh` is meant to run from on first boot.

No Thor restarts; none of this touches Thor.
