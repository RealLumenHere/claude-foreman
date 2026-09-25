# wq#2573 — lumen-reflector deadman + proposal backlog funnel

wq#2564 audit finding: `lumen-reflector.service` (Result=exit-code,
ExecMainStatus=1) failed 2026-09-20 18:30 UTC and was still failed 5 days
later with no ticket. wq#2188 separately found 36 unapplied memory
proposals in `minerva/dream/reflections/`, oldest 87 days: proposals are
produced faster than Frank reviews them.

## What actually broke, and why the fleet didn't notice

`lumen_reflector.py` already fails loud by design (exits non-zero + posts
an ntfy alert on any LLM/network error — see its own docstring). The gap
isn't that fail-loud behavior; it's that nothing durable backed it up:

1. `alert()`'s ntfy POST was wrapped in a bare `except Exception: pass` —
   if ntfy itself was unreachable, the only failure signal vanished with
   zero trace.
2. The unit is `Type=oneshot` with the fleet's standard dead-man stamp
   wired via `ExecStartPost=-/home/lumen/bin/lumen-cron-stamp %n`
   (`ops/wq1184-gatus-health-checks`). `ExecStartPost=` does not run when
   `ExecStart` exits non-zero (systemd.service(5)), so a fail-loud exit
   never touches the stamp. `cron_deadman.py`'s staleness check only fires
   at 2x the timer's cadence — 14 days for this weekly job — so a failure
   like this one can sit invisible for up to two weeks, well past the 5
   days it took wq#2564's manual audit to catch it.

Fix (both changes land as real, tested diffs — see PRs below):

- `lumen_reflector.py` (minerva repo): every exit path now calls
  `write_run_state('ok'|'error', detail)`, which (a) unconditionally
  refreshes the `.stamp.lumen-reflector` liveness file regardless of
  ExecStartPost, and (b) writes `.state.lumen-reflector` with the last run's
  status. `alert()` now logs a failed ntfy POST to stderr instead of
  swallowing it.
- `cron_deadman.py` (this repo, `ops/wq1184-gatus-health-checks`): reads
  `.state.<unit>` for any unit it already tracks a stamp for, and pages
  immediately (once, deduped, with a matching recovery notice) on
  `status: "error"` — independent of stamp age. See
  `test_cron_deadman.py::test_fresh_stamp_with_error_run_state_pages_immediately`
  and `::test_error_run_state_pages_once_until_recovered`.

The `lumen_reflector.py` change lives in the **minerva** repo, not this one
(same cross-repo shape as wq#2569 — dispatcher checked this ticket out as a
`foreman` worktree, but its named deliverable lives in a second repo this
worktree can't natively diff against). Delivered via a nested worktree at
`_probe/minerva-wt` (gitignored) on branch `fix/wq2573-reflector-deadman`,
landed separately through `lumen-pr` from inside that nested tree; see
`wq2573_cross_repo_evidence.md` at this worktree's root for the exact PR
link and citation lines.

## Backlog funnel + aging (AC2/AC3, wq#2188)

`backlog_digest.py` is a standalone, repo-independent tool (parameterized by
`--reflections-dir`, no hardcoded `/home/lumen/minerva` path) with two
subcommands:

```
backlog_digest.py funnel --reflections-dir DIR [--applied-log PATH]
backlog_digest.py digest --reflections-dir DIR --out PATH [--age-days N]
```

- `funnel` prints proposals/week created vs reviewed vs applied. Reviewed/
  applied read 0 unless `--applied-log` (one promoted proposal title per
  line) is supplied — **there is no such ledger today**, which is the
  literal wq#2188 finding restated as code instead of prose: the backlog
  has no working review funnel. The moment Frank (or `fq promote`) starts
  recording promotions to a log, this tool's numbers become real without
  any code change.
- `digest` re-batches every reflection older than `--age-days` (default 14)
  into one combined markdown file for a single review pass, instead of N
  separate stale files. It only writes the digest — source reflection files
  are left untouched, and no proposal is ever applied to `claude_memory.md`
  or `instincts.yaml` (instinct 7).

This worker's sandbox has no path to the real
`/home/lumen/minerva/dream/reflections/` (same cross-repo constraint as
above, compounded by that directory being untracked/gitignored even inside
the minerva repo), so `backlog_digest.py` is tested against synthetic
fixtures (`test_backlog_digest.py`, 7 tests, matching
`lumen_reflector.py`'s actual JSON schema and PT timestamp format) rather
than the live 36-item backlog. Running it against the real directory —
`backlog_digest.py funnel --reflections-dir /home/lumen/minerva/dream/reflections`
— is the natural next step to put real numbers on wq#2188 and produce its
first digest; this ticket delivers the tool, not that run (same shape as
wq#1747/wq#1968's "worker can't read secrets, ships the script").
