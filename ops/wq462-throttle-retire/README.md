# lumen-throttle-{on,off}: retired (wq#462)

## Decision: retire, not retarget

`lumen-throttle-on/off` capped upload to 5mbit 06:00-07:35 UTC by applying a
`tc htb` qdisc to `enp0s25`. That NIC has been dead since the 2026-08-02
single-NIC cutover (`ens1`, 2.5GbE, is now the sole uplink; `enp0s25` is fully
unmanaged and doesn't enumerate) — so the unit fired daily, failed to find the
device, and threw an R7 `failed-state` finding (aq#451) without ever actually
applying the cap.

Evidence against retargeting to `ens1`:

- The cap has been silently absent for 3+ weeks (unit broken since ~08-08,
  cutover 08-02) with zero reports of upload contention during the window —
  if the protection still mattered, someone would have noticed by now.
- The prior ops audit (wq#752, PR #49,
  `reports/opsaudit_a2_frank_backlog.md`) classified wq#462 as
  agent-answerable today, not a genuine Frank-only decision — the "protect a
  window" policy predates the 2.5G single-NIC uplink and its original
  rationale isn't recoverable from any record; nothing currently depends on
  it.
- Retiring is trivially reversible: the unit files are preserved as
  `.disabled.bak`, not deleted. If upload contention resurfaces during
  06:00-07:35 UTC, restoring (with `s/enp0s25/ens1/` on the two `tc` lines)
  is a two-minute job.

## What changed

Host `/etc/systemd/system/` (no repo files — these units were never checked
in):

- `lumen-throttle-on.timer` / `.service` — already renamed to
  `.disabled.bak.20260810` by the 08-10 aq#451 response; not enabled, not
  enumerating in `systemctl list-unit-files`, skipped by `lumen-unit-lint`
  (`.bak` suffix).
- This ticket normalized the naming to match the fleet-wide convention (every
  other retired unit uses plain `.disabled.bak`, no date stamp): renamed to
  `lumen-throttle-{on,off}.{service,timer}.disabled.bak` via
  `lumen-run --root`. No functional change — both were already inert.

Verified post-rename: `systemctl is-enabled lumen-throttle-{on,off}.timer` ->
`not-found` for both; no `*.wants/` symlinks reference either unit.
