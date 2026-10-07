# wq#3584 — root-apply wq3354-fleet-selfheal (class 8 exposure census, wq#3514)

Author: claude-sonnet-5 (build worker, worktree `wq3584-a1`).

## Starting state (stale busy key)

`lumen_busy check` returned a stale lock from an unrelated, already-resolved ticket
(`{"task": "wq3581 verify wq3496 node-bench P1 3.8GiB", "phase": "research"}`) — wq#3581's
judgment already landed as commit `ae408f4c` in this branch's own history. Cleared it before
starting this ticket's work.

## Before: live gap matched the ticket exactly

`bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify` (run via
`lumen-run start --name wq3584verify`, unit `lumen-job-wq3584verify-1791379354`), exit 1:

```
MISS absent: /home/lumen/agents/fleet-selfheal/exposure_census.py
MISS absent: /etc/systemd/system/lumen-exposure-census.service
MISS absent: /etc/systemd/system/lumen-exposure-census.timer
MISS not enabled: lumen-exposure-census.timer (not-found)
MISS not active: lumen-exposure-census.timer (inactive)
VERIFY FAIL (5)
```

## Install

`lumen-run start --root --name wq3584install -- bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh`
(unit `lumen-job-wq3584install-1791379362`), exit 0. The script's own pytest gate
(`ops/wq3354-fleet-selfheal/install.sh:47-48`) ran against `ops/wq3354-fleet-selfheal/tests`
before touching anything live — no `REFUSING` line in the unit log, so the suite passed.
Code + all five units installed, sweep/leakwatch dry-runs printed clean, and
`systemctl enable --now` created
`/etc/systemd/system/timers.target.wants/lumen-exposure-census.timer`
(`ops/wq3354-fleet-selfheal/install.sh:66-67`) alongside the pre-existing sweep timer and
leakwatch service.

## Live config merge (install never clobbers the operator's selfheal.yaml)

Diffing the live `/home/lumen/agents/fleet-selfheal/selfheal.yaml` against this repo's copy
showed exactly two gaps: the `lumen-exposure-census.timer` entry missing from
`guards.timers`, and the whole `exposure:` CLASS 8 block (repo
`ops/wq3354-fleet-selfheal/selfheal.yaml:29,122-142`) absent. Backed up the live file
(`selfheal.yaml.pre-wq3584.bak`, via `lumen-run`), then ran an idempotent patch script
(`/tmp/fworker-wq3584/patch_selfheal_yaml.py`, via `lumen-run start --name wq3584patch`) that
inserted the timer line after the `litellm-completion-probe.timer` anchor and appended the
`exposure:` block verbatim. Re-diffing afterward showed only one remaining difference: an
unrelated `ollama:` CLASS 7 block (wq#3558) that the live file has never carried — out of
scope here, same as wq#3570's precedent (`reports/wq3570_root-apply_wq3354-fleet-selfheal_ff-minerva.md`),
since `sh_ollama.py`'s own defaults apply when the section is absent.

## After: VERIFY OK

`bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify` (unit
`lumen-job-wq3584verify3-1791379478`): `VERIFY OK`, exit 0 — covers code parity for all ten
`CODE[]` files, `selfheal.yaml` presence, all five systemd units byte-matching, and both
`lumen-fleet-selfheal.timer`/`lumen-leakwatch.service` plus the new
`lumen-exposure-census.timer` enabled+active.

Direct systemd confirmation (`systemctl show lumen-exposure-census.timer`,
unit `lumen-job-wq3584systat-1791379607`): `ActiveState=active`, `UnitFileState=enabled`,
`NextElapseUSecRealtime=Thu 2026-10-08 05:27:56 UTC`.

A live `exposure_census.py --dry-run` run (unit `lumen-job-wq3584census-1791379488`) was
kicked off as an additional runtime check; it is a full content-driven walk of
`/home/lumen` capped at `time_cap_s: 1500` (~25 min) and was still running at report time —
not required for this ticket's liveness gate (`ops_verified(wq3354-fleet-selfheal)`, which
`install.sh --verify` already satisfies), so not blocked on here.

## Verdict

wq#3354's class 8 exposure census bundle is now fully live: code + units installed, timer
enabled and active, and the operator's live `selfheal.yaml` carries the merged `exposure:`
config without losing any of its existing content.
