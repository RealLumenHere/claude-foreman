## What this is

wq#2178 asked a build worker to apply the wq#2166 gpu-lowpower bundle to hathor and
nephthys. It could not be applied: no sanctioned path exists from a build worker to write
to a fleet node. This PR lands the evidence and the unblock options instead of a fake
green.

## The drift is real and still live

Re-confirmed 2026-09-20 from Lumen over ticket 2178's read-only `fleet-exec run` grant:
both of hathor's D300s read `auto`, while `gpu-lowpower.service` reports
`ActiveState=active` / `ExecMainStatus=0` — the exact false-green wq#2166 was built to
make impossible. `ops-deploy-drift` should keep its
`ops-drift:wq2166-gpu-lowpower-ordering` row open.

## Why it could not be applied

Every candidate path was checked, and each one is recorded with its exact refusal in the
report:

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

## Unblock

One of: a `copy` grant plus an implemented `fleet-exec sudo` verb; `Bash(ssh *)`/
`Bash(scp *)` on the worker allowlist with a sudo carve-out; or an operator running the
two `RUNBOOK.md` §Apply commands by hand. The report recommends the operator route for a
one-off.

Also worth noting: `fix/wq2166-ops-hathor-gpu-lowpower-service-reports--a1` is not merged
to `main`, so the bundle to be applied currently lives only on that branch.

The ISSUE-LOG row (#476) is included in the report body — `/home/lumen/kb` is
guard.sh-blocked for workers, so it could not be appended directly.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
