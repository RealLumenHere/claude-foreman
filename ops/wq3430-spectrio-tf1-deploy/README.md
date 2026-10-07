# wq#3430 — [spectrio] Deploy 5 TF1 single-purpose appliances (NOT a cluster)

## Decision record

Frank, 2026-10-02: count dropped 24 → 5. The earlier cluster framing (lab F /
arm64 pool) is **dropped** — it needs more nodes than this project has. This
supersedes the "cluster kit" (roles F+A, N-parametric) left at
`projects/spectrio-tf1/cluster/` (ARCHITECTURE.md, README.md) by a prior
session on 2026-10-06 — that note flagged a conflict with this ticket's 24→5
call and asked Frank to confirm; the ticket text already carries that
confirmation (dated 2026-10-02, before the cluster kit was written), so no
new decision is needed here. That directory is outside this worktree's
allowed paths and is left as-is; a follow-up ticket should retire or archive
it once this plan lands.

5 appliances, one job each, **identical base image** (`ophub Armbian
S905X3`, proven by wq#3429's flash procedure) — roles are config-only, so
any of the 5 boxes can swap into any role by re-applying `roles.yaml`. See
that file for the exact per-role fields; `render_role.py` validates the
schema and renders the first-boot config-only diff per role
(`python3 render_role.py --validate`, `python3 render_role.py <role-id>`).
`test_render_role.py` (10 cases, `python3 -m pytest test_render_role.py`)
covers all 5 roles plus negative cases (missing static IP, tailnet set on a
DNS resolver, under-provisioned serial count) so a broken role file fails
loudly instead of shipping silently.

## Build order (fixed in `roles.yaml: build_order`)

1. **pathfinder-spare** — built and proven first. Runs wq#3429's
   census+sbc-bench+power+UART+flash-procedure tooling. Its image, once
   proven, is cloned to the other 4 — this is why it goes first and why it
   stays a hot spare afterward rather than being retired.
2. **klipper-host** — Klipper/Moonraker/Fluidd, replaces the 3dprinter LG
   AIO.
3. **adguard-primary** / **adguard-secondary** — DNS failover pair, static
   IPs, no tailnet; fills the unbuilt Network-Guardian sinkhole gap.
4. **kiosk-dashboard** — Chromium/Wayland kiosk, HDMI 1080p, targets
   Grafana (Bes) or Glance.

## What ships in this ticket

Everything that does not require hands on the hardware: the role schema,
validator/renderer, and tests above, plus this plan. Per the standing
instinct that a physical act nobody but Frank (or someone on-site) can
perform gets filed as a queued task rather than left as a blocking gate,
the physical steps are enumerated per-role in `roles.yaml` under each
role's `physical_tasks:` key and summarized here:

- **Bench/flash**: flash pathfinder first with the wq#3429-proven image;
  only after it's proven, clone to the remaining 4. No session on this
  fleet has bench or fleet-exec access to these units yet (`fleet-exec
  grant` → `DENY [grant:grant_missing_env]` from this worktree) — same
  class of irreducible blocker as the purchase in wq#3319
  (`ops/wq3319-a1/README.md`).
- **AIO config pull**: `printer.cfg` and `moonraker.conf` must be pulled off
  the LG AIO *before* any write to it. The AIO has been tailnet-offline
  since 2026-07-26 but may still be LAN-reachable — confirm reachability
  first, over LAN or physically, before assuming a teardown is needed.
- **USB serial**: the board's onboard USB port count is unconfirmed
  (`UNK` per the ticket). klipper-host needs >= 2 serial devices
  (`roles.yaml: klipper-host.serial_devices_min: 2`) — add a hub if the
  pathfinder unit turns out to have fewer once it's in hand.
  `render_role.py` already encodes the >=2 requirement so a role file that
  regresses it fails validation, not silently at boot.
- **Static IP assignment**: both AdGuard units need a fixed IP out of the
  DHCP pool, assigned at bench time and recorded back into `roles.yaml`
  (currently `TBD` placeholders, intentionally — a real IP shouldn't be
  guessed into a committed file).
- **Display/decode check**: HDMI 1080p output and hardware video decode
  under Wayland are unverified on this SoC for the kiosk role; verify on
  first boot and fall back to software decode if it fails.

None of the above block landing this plan — they're pending bench work,
tracked in-line so the next ticket with real hardware/fleet-exec access is
a fill-in-the-blanks apply (static IPs, confirmed serial count, confirmed
AIO reachability) rather than a research task.

## Deps

wq#3429 (done — flash procedure + census tooling at
`/home/lumen/projects/spectrio-tf1/census/CENSUS_METHODOLOGY.md`, bench
execution itself still Frank-gated per that ticket's own notes).

No Thor restarts; none of this touches Thor.
