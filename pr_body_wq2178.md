## What

wq#2178 asked for the wq#2166 gpu-lowpower bundle to be applied to hathor and nephthys.
It could not be applied: no sanctioned path exists from a build worker to write to a fleet
node. This PR lands `reports/2178_gpu_lowpower_apply_blocked.md` — the live drift
evidence, every refused path with its exact message, and the unblock options — instead of
a fake green. The ticket is blocked, not closed.

## Why

The drift is real and still live. Re-confirmed 2026-09-20 from Lumen over ticket 2178's
read-only `fleet-exec run` grant: both of hathor's D300s read `auto`, while
`gpu-lowpower.service` reports `ActiveState=active` / `ExecMainStatus=0` — the exact
false-green wq#2166 was built to make impossible. `ops-deploy-drift` should keep its
`ops-drift:wq2166-gpu-lowpower-ordering` row open; that alarm is correct.

Recording why the apply is structurally impossible is worth more than another redispatch
into the same wall — wq#2166's worker already hit one face of it.

## How

Checked every candidate write path and recorded its exact refusal:

- `ssh` / `scp` are absent from the worker allowlist; `sudo *` is on the explicit deny
  list (`harness/worker-settings.json:118`).
- `bash ops/*` is not allowed either, so even the read-only `install.sh --verify` cannot
  run.
- `fleet-exec copy hathor … /home/hathor/lumen-drop` → `DENY [grant:no_copy_dir]`.
- `fleet-exec restart hathor gpu-lowpower.service` → `DENY [grant:no_restart_unit]`.
- `fleet-exec` has no sudo verb at all — `sudo_cmds` in `harness/fleet-exec-policy.yaml`
  is a v2 allowlist for a verb that does not exist.

`ssh` was reachable by wrapping it in an allowlisted interpreter. That is evasion of an
explicit refusal, and the thing it would run is hard-denied `sudo`, so it was not done.

Unblock needs one of: a `copy` grant plus an implemented `fleet-exec sudo` verb;
`Bash(ssh *)`/`Bash(scp *)` on the worker allowlist with a sudo carve-out; or an operator
running the two `RUNBOOK.md` §Apply commands by hand. The report recommends the operator
route for a one-off.

## Testing

No code changed — this PR is one report plus this body. The evidence in it is live output,
each command and result quoted in the report:

- `fleet-exec run hathor cat /sys/class/drm/card1/…/power_dpm_force_performance_level` → `auto`
- same for `card2` → `auto`
- `fleet-exec run hathor systemctl show gpu-lowpower.service -p ActiveState -p ExecMainStatus`
  → `ActiveState=active`, `ExecMainStatus=0`
- `fleet-exec run nephthys hostname` → `nephthys` (reachable read-only)
- the two `DENY` lines above, reproduced verbatim

wq#2166's own 19 tests were not re-run; this PR does not touch that branch.

## Risk / rollback

None. Documentation only, on no execution path. Rollback is reverting the two commits.

The standing risk is unchanged and untouched by this PR: hathor's GPUs stay at `auto`
until someone with root applies the bundle.

## Notes

`fix/wq2166-ops-hathor-gpu-lowpower-service-reports--a1` is not merged to `main`, so the
bundle to be applied currently lives only on that branch.

The ISSUE-LOG row (#476) is included in the report body rather than appended —
`/home/lumen/kb` is guard.sh-blocked for workers (previously recorded as ISSUE-LOG #473).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
