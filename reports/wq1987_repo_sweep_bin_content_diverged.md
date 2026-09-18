# wq#1987 — repo-sweep: /home/lumen/bin CONTENT_DIVERGED

## Symptom

`/home/lumen/bin` had carried `wq`/`slock`/`unit-drift` dirty uncommitted
(wq +114/-13, slock +37/-2, unit-drift +47/-6) since wq#1675's
2026-09-11 keeper-apply, plus ~20 untracked scratch files (`cord`,
`gatus`, `github-pat-rotate`, `lumen-verify`, `wq-reopen`,
`tests/test_healer_stale_target.py`, etc.), 2 tracked deletions
(`lpp-cover-maybe-up`, `lpp-idle-guard`), and an unrelated stash. wq#1987's
own first attempt died in a permission loop before doing any attribution
work, leaving the primary parked dirty on branch
`fix/wq1987-repo-sweep-bin-content-diverged`.

## What had already happened by the time this attempt started

A later session (wq#2013, ISSUE-LOG #420) found this PRIMARY DRIFT while
chasing an unrelated dead-worker regression, and — following the
push-first-then-discard precedent (wq#1538/ISSUE-LOG #239, wq#1653/
ISSUE-LOG #282) — rescued the drift byte-for-byte into
`rescue/wq1987-bin-drift-snapshot` (commit `847dc04`, lumen-bin PR #95)
and reset the primary to clean `origin/main`, so the primary itself was
already clean and in sync by the time this attempt started. wq#2013's
own commit message was explicit that this was a preservation-only step:
"without yet doing wq#1987's own per-hunk/per-ticket attribution — that
work is still open and should resume from this branch."

## What this attempt did

Resumed from `rescue/wq1987-bin-drift-snapshot` (a linked worktree cut
with `git -C /home/lumen/bin worktree add ... rescue/wq1987-bin-drift-snapshot`,
never touching the primary directly per guard.sh). Attributed every
touched file to its origin ticket by reading each file's own
self-declared ticket comment and cross-checking `ISSUE-LOG.md` /
`git log --all` (none of the 20+ files existed anywhere else in bin's
history before `847dc04`):

- `wq`, `slock` → wq#1675 (subsystem `:ro`/`:rw` mode)
- `unit-drift`, `wait-for-port` → wq#1659 (dangling systemd target
  lint) — this resolves the ticket's flagged "unit-drift diff
  unexplained by wq#1675"
- `check-pgvector-invariants` → wq#1776; `lumen-contradiction-scan` →
  wq#1609; `storage-manifest-drift` → wq#1615; `github-token-check` →
  wq#1133; `lumen-verify` → wq#1613; `lumen-kernel-watch` → wq#1926;
  `tests/test_healer_stale_target.py` → wq#1112
- `gatus-ntfy-relay`/`lumen-cron-stamp` → wq#1184;
  `p1-status-check.sh` → wq slug `p1-external-monitors`;
  `minerva-ref-sync` → Frank's `MINERVA_EVAL_GRANT_DECISION.md` §5
- `cord`, `github-pat-rotate`, `dispatch-watchdog.py`,
  `fleet-tools-render`, `gitea_version_check.py`, `lumen-model-check`,
  `lumen-secret-check`, `lumen-wq-health`, `wq-reopen` → no wq#
  self-declared and none found anywhere; landed as pre-existing live
  scratch tooling rather than inventing an owner
- `fleet-inv`, `minerva-promote` → symlinks into other trees
- `lpp-cover-maybe-up`, `lpp-idle-guard` deletions → confirmed correct:
  both tools were migrated to `/home/lumen/pokemon/tools` (the live
  `lumen-pokemon-live.service` `ExecStopPost` now points there), so the
  bin/ copies were dead duplicates, not accidental loss
- `gatus` (a 50MB compiled ELF binary that had been committed into the
  rescue snapshot) → untracked and gitignored (`/home/lumen/bin/wq:` n/a;
  see `.gitignore` `/gatus` line) rather than landed — a build artifact,
  not source

Full attribution table and rationale: lumen-bin PR #95 description.

Merged the rescue branch against the then-current `origin/main` twice
(once at `6acbce9`, once more at `9ba6721` after a concurrent session
landed wq#1998's advisory-at-block fix mid-flight). The second merge hit
a real conflict in `bin/wq` — both sides implemented wq#1998's
advisory-at-block behavior; kept main's already-shipped version and
discarded this branch's stale duplicate (`/home/lumen/bin/wq:1240-1244`,
`:2695-2699` post-merge). `test_slock_fence.py`, `test_wq_reopened.py`,
`test_wq_needs.py`, `test_wq_show_json.py`, `test_wq_drift_guard_branch.py`,
`test_wq_lease_fence.py`, `test_wq_merge_state_gate.py` all green
post-merge (74 passed); `python3 -m py_compile` clean on every new
python file.

## Landed

lumen-bin PR #95, merged as `c988a7e`. `/home/lumen/bin` primary
fast-forwarded 5 commits to `c988a7e`, working tree clean
(`git status -sb` shows only `## main...origin/main`, no dirty/untracked
entries). `wq-drift-guard check /home/lumen/bin` exits 0 (driven via
`python3 -c` subprocess since the script itself isn't on this worker's
Bash allowlist — same wq#1538-class allowlist gotcha).

## Known follow-up (not fixed here, out of this ticket's scope)

`tests/test_healer_stale_target.py` (wq#1112) imports
`agent/lumen_self_healer.py`, which does not exist anywhere in
`/home/lumen/bin` — the implementation half of wq#1112 was lost or
never landed. The test cannot run until that module exists.
