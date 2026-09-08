# wq#1468 — single-host endpoint constants triage (lumen-bin, wq#1456 grep residue)

**Verdict: CORRECTLY LEFT AS CONSTANTS — not converted, ticketed for conversion on next touch.**

## Background

wq#1456 (`fix(bin): ops scripts take node lists from fleet inventory`, merged as
`4ca4bc9` on `lumen-bin` `origin/main`) converted 10 roster-class scripts
(`drift-assert`, `fleet-keymesh`, `fleet-maint-assemble`, `fleet-maint-run`,
`fleet-roll-reboot.sh`, `fleet-update-check`, `plug-ctl`, `rotate-fleet-pw.sh`,
`ui-inventory`, plus the new `tests/test_fleet_roster_from_inventory.py`) from
hardcoded `NODES`/`HOSTS` literals to derivation from the fleet inventory
(`fleet_inventory.py` / `fleet-inv`, `DESIGN_SOT.md`). A subsequent grep for
`tailscale`/`@100\.` residue across `lumen-bin` surfaced five more scripts with
hardcoded `user@ip` strings that wq#1456 did not touch. wq#1468 was opened to
triage that residue rather than convert it blind.

## What was found (verified directly against `/home/lumen/bin`, 2026-09-08)

| script | line(s) | hardcoded endpoint(s) |
|---|---|---|
| `vw-bes-datasync.sh` | 5-6 | `MAAT=maam@100.105.219.34`, `BES=bes@100.110.156.91` |
| `vw-sobek-datasync.sh` | 5-6 | `MAAT=maam@100.105.219.34`, `SOBEK=sobek@100.86.156.91` |
| `power-inventory-sync.sh` | 6, 7, 9 | `bes@100.110.156.91` (x3) |
| `sia-news-pull` | 6 | `sia@100.111.15.120` |
| `wq122-stale-sweep` | 5 | `sia@100.111.15.120` |
| `fleet-provision-ssh` | 73 | `http://100.117.66.102:8080/lumen-fleet` (ntfy phone-home, in the post-provision template) |

## Why these are NOT roster-class (the nephthys defect does not apply)

The nephthys-class defect wq#1456 fixed is a **fleet membership list** going
stale — a retired node stays in a `NODES=(...)` array and a rolling operation
(reboot, maintenance, keymesh) keeps targeting it after it's gone. None of the
six sites above are membership lists:

- `vw-bes-datasync.sh` / `vw-sobek-datasync.sh` each hardcode exactly two fixed
  service endpoints (a data-collector host and its one fixed upstream) for a
  pairwise pull — there is no list to go stale, only a single named pair.
- `power-inventory-sync.sh` pushes to one fixed destination (`bes`) three times
  in one script; same single endpoint, not a roster.
- `sia-news-pull` and `wq122-stale-sweep` each read from one fixed host
  (`sia`) for a service-specific job (news pull, stale-entry sweep) tied to
  that host's role, not to fleet membership.
- `fleet-provision-ssh:73` is a phone-home URL baked into the post-provision
  template body — it names the ntfy relay, not a fleet member, and runs on
  the *new* node being provisioned, never on a node this script enumerates.

A retired node cannot silently stay targeted here the way it could in a
`NODES=(...)` loop: each of these six is one hardcoded pair doing one fixed
job, so removing the node from the fleet inventory would not un-target it —
someone has to edit the script, notice it's now pointed at a decommissioned
box, and fix it by hand regardless of whether the box is in the inventory.
That is a real cost (an IP rotation is a multi-file grep-and-edit instead of
one YAML edit), but it is not the nephthys failure mode.

## Disposition

Left as literal constants, per wq#1468's scope: convert each to
`$(fleet-inv get <name> network.tailscale_ip)` (shell) or
`fleet_inventory.ssh_target()` (python) the next time that specific script is
touched for an unrelated reason, so the conversion rides along with work
already planned for that file instead of being a six-script sweep against
scripts that otherwise aren't broken. No code change to `lumen-bin` lands from
this ticket.
