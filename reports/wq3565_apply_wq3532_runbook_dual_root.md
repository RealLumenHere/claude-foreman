# wq#3565 — apply wq#3532 runbook: dual-root fixes (step 2)

## Outcome
Step 2 landed: lumen-bin PR #232 (`fix/wq3565-dual-root-projects`, merged
f4f6ad76d7, squash-merged by `lumen-pr land`, primary `/home/lumen/bin`
fast-forwarded to b6ee622).

- `fleet-search:39-48` — added `PROJECTS_NEW`/`PROJECTS_OLD` and `_project_root(project)`,
  which prefers `/scratch/fleet/projects/<name>` when it exists, else falls back to
  `/home/lumen/projects/<name>`.
- `fleet-search:190-198` — `_cache_paths()` calls `_project_root()` instead of hardcoding
  the deprecated root, so `--project` cache creation no longer mkdir-resurrects
  `/home/lumen/projects/<name>` for an already-migrated project.
- `wq:2163` (`/home/lumen/bin/wq`, post-merge) — `_sha_repo_candidates()` now also scans
  `/scratch/fleet/projects` as a repo base.
- `gitea-rotate-finish:31-32` — `REPOS` default updated from
  `/home/lumen/projects/lumen-messenger-cc` to `/scratch/fleet/projects/lumen-messenger-cc`,
  verified live with `test -d` before the edit (old path does not exist; new path does).

## Step 1 — blocked, logged
`apply.sh` (foreman `ops/wq3532-projects-migration-finish/apply.sh`) needs root + /etc
write + `/home/lumen/projects` write + fleet-units write. This fworker BUILD session's
sandbox hard-blocks the root-escalation verb (guard.sh denied-verb) and its allowed
directories do not include `/etc` or `/home/lumen/projects`. Confirmed still-unapplied via
`systemctl cat lumen-bench.service` showing `WorkingDirectory=/home/lumen/projects/bench`
(old path) live on 2026-10-07. Logged to `/home/lumen/kb/ISSUE-LOG.md` row 1201. Needs a
privileged (root) session to run `apply.sh`; not a Frank judgment call, a sandbox-capability
gap.

## Evidence
- PR: http://100.121.17.5:3000/lumen/lumen-bin/pulls/232
- Test run at commit time (via `lumen-pr commit`): 10 passed, 0 failed.
- ISSUE-LOG.md row 1201 records the step-1 blocker.
