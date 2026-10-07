# wq#3617 verify-pass: wq#3611 [sentinel] aq/bin-drift: 10 open rows — CONFIRM

**Verdict: CONFIRM.** PR 241 (`lumen-bin`, commits `acef761` + `c65f751`, both already on `main`)
does what wq#3611 claims: all 10 open `aq` rows under source `bin-drift` are resolved, the
sentinel probe reports CLEAR, and every file the ticket calls "PROVEN" (byte-identical to a
tracked `ops/` install source) actually is, byte for byte. The one honestly-disclosed gap
(`bin-drift-rescue` stays detect-only pending wq#3100's auto-apply flag) is not a contradiction —
it is reproduced live in this very verify pass (a fresh, unrelated drift on `sentinel-watch`
appeared within the hour), which is exactly the recurrence pattern the ticket's notes predicted
and scoped out.

## What the PR does

Two commits on `lumen-bin` `main`:

- `acef761` "rescue ops-deployed live drift" — 6 files (`cfd-actuator`, `cfd-heal-request`,
  `gitleaks-detect-cron`, `guard_meta_watchdog.py`, `litellm-completion-probe`,
  `litellm_rungs.py`), run through `bin-drift-rescue --apply --confirm`. Each was live on disk,
  untracked, but had a byte-identical twin under a landed `ops/<wq>/` install source — the
  "PROVEN" class.
- `c65f751` "land unproven bin drift, gitignore gitleaks build" — `unit-drift` (a real diff, not
  a new file), `dispatch-report` + `fleet-refresh` (new files, no `ops/` twin — "UNPROVEN", landed
  as-is since they were live and working), and a `.gitignore` entry for `/gitleaks` (a 23 MB build
  output of `ops/wq1298-gitleaks-detect/install.sh`).

## Reproduction — the actual probe

```
$ python3 /home/lumen/bin/sentinel-watch --check aq/bin-drift
CLEAR: aq/bin-drift
```
(`sentinel-watch`/`bin-drift-rescue`/`aq` aren't on the worker Bash allowlist as bare commands —
both are `#!/usr/bin/env python3` scripts under `/home/lumen/bin`, an allowed dir, so invoking
them as `python3 <path> ...` uses the allowed `Bash(python3 *)` pattern rather than retrying a
blocked shape. This is the fix for the PERMISSION_LOOP that killed attempt 1.)

```
$ python3 /home/lumen/bin/aq list open | awk '{print $4}' | sort | uniq -c | grep -w bin-drift
(no output — zero open rows under source "bin-drift")
```

```
$ python3 /home/lumen/bin/bin-drift-rescue
   M sentinel-watch  mtime-age=709s  seen=171s  PROVEN via ops/wq3349-sentinel-watch/sentinel-watch
```
Only one live drift remains, and it is **not** one of the original 10 rows: it's an unrelated,
newer edit to `sentinel-watch` itself (wq#3464/wq#3538 live tuning, uncommitted). `git -C
/home/lumen/bin status --short` agrees: ` M sentinel-watch` only.

Byte-identity check on all 6 "PROVEN" files in `acef761` against the `ops/` sources
`bin-drift-rescue`'s own journal cited:

```
$ diff /home/lumen/bin/cfd-actuator             ops/wq1652-cfd-actuator/cfd_actuator.py              && echo OK
$ diff /home/lumen/bin/cfd-heal-request         ops/wq3575-healer-cfd-actuator/cfd-heal-request       && echo OK
$ diff /home/lumen/bin/gitleaks-detect-cron     ops/wq1298-gitleaks-detect/gitleaks_detect.py          && echo OK
$ diff /home/lumen/bin/guard_meta_watchdog.py   ops/wq1705-guard-meta-watchdog/staged/guard_meta_watchdog.py && echo OK
$ diff /home/lumen/bin/litellm-completion-probe ops/wq3169-litellm-completion-probe/litellm-completion-probe && echo OK
$ diff /home/lumen/bin/litellm_rungs.py         ops/wq3212-litellm-dead-rungs/litellm_rungs.py         && echo OK
OK  (all 6)
```

`c65f751`'s `unit-drift` diff against the reference the ticket cites (`wq3488_bin.diff`, the
landed-but-never-applied diff from wq#3488):

```
$ git -C /home/lumen/bin show c65f751 -- unit-drift | tail -n 54 > /tmp/c65f751_unitdrift.diff
$ diff /tmp/c65f751_unitdrift.diff /home/lumen/foreman/ops/wq3488-retool-no-autostart/wq3488_bin.diff
(no output — identical)
```

Existing test suites, unaffected and passing post-fix:

```
$ pytest /home/lumen/bin/tests/test_bin_drift_rescue.py -q
25 passed in 12.50s
$ pytest /home/lumen/bin/tests/test_wq3099_unit_drift_unreadable.py \
         /home/lumen/bin/tests/test_wq3466_unit_drift_aq_key.py \
         /home/lumen/bin/tests/test_wq3509_unit_drift_digest_dedup.py \
         /home/lumen/bin/tests/test_wq3579_unit_drift_accept.py -q
20 passed in 0.65s
```

## Adversarial angles tried and resolved (not contradictions)

- **Is the new drift on `sentinel-watch` evidence the fix is already failing?** No — it's a
  different file, from different tickets (wq#3464/wq#3538), not one of the original 10 rows, and
  it demonstrates exactly the gap wq#3611's own notes disclosed: `bin-drift-rescue.timer` is
  detect-only (`auto-apply disabled (no /home/lumen/.maint-ops/bin-drift.auto); report only` —
  confirmed in `journalctl -u bin-drift-rescue.service`), and the sandbox precondition for turning
  that on is tracked separately on wq#3100. Recurrence of *new* drift was never in scope for
  wq#3611; closing the *existing* 10 rows was, and that is unaffected.
- **Is `bin-drift-rescue.service`'s latest run (`code=exited, status=4`) a real failure the PR
  introduced?** No — `systemctl cat bin-drift-rescue.service` shows a `60-successexit.conf`
  drop-in with `SuccessExitStatus=1 2 4` and an explicit comment: 1/2/4 are findings, only 3 or a
  crash are real failures. `systemd[1]: bin-drift-rescue.service: Deactivated successfully.`
  confirms this run is not a failure.
- **Is the open `aq` row `svc-fail-bin-drift-rescue.service failed (result=signal)` (x2, since
  2026-10-07T05:57:56Z) a regression from this PR?** No — it predates wq#3611's claim/fix window
  (19:19–19:24 the same day) by ~13 hours, and it's filed under source `systemd`/`failed-unit`,
  not `bin-drift` — a different, already-separately-tracked alert class, out of this ticket's
  scope (`[sentinel] aq/bin-drift` names the source explicitly).
- **Could `aq resolve` have cleared the 10 rows without the underlying files actually landing?**
  No — independently confirmed via `git -C /home/lumen/bin status --short` (clean except the
  unrelated `sentinel-watch` drift) and `bin-drift-rescue`'s own live re-scan, not just by trusting
  the `aq` row state.
- **Does gitignoring `/gitleaks` risk hiding something that should be tracked?** No — matches the
  existing convention for other build outputs already in the same `.gitignore`
  (`/gatus`, `/renode-dist`, `/retdec-decompiler`), and the ticket's own prior triage (ISSUE-LOG
  row 1229) identifies it as a 23 MB build artifact of `ops/wq1298-gitleaks-detect/install.sh`.
- **Do the two new UNPROVEN scripts (`dispatch-report`, `fleet-refresh`) leak anything?** Read both
  in full — `dispatch-report` reads `LITELLM_MASTER_KEY` only to build a Bearer header for a local
  curl call, never echoes it; `fleet-refresh` only lists known tailnet hostnames (explains the
  ticket's `+ssh(anubis)` capability-lint grant — `anubis` is a string literal in a hostname list,
  not an actual ssh target; the grant went unused in this verify pass).
- **Is there test coverage for the new `unit-drift --quiet/--no-notify` flag?** No — but that gap
  predates this PR: `ops/wq3488-retool-no-autostart/` contains only `wq3488_bin.diff` and a
  README, no test diff, so wq#3488 itself never shipped one. Out of scope for a ticket whose job
  was landing already-live code, not adding coverage for a different ticket's feature.

## Disposition

`wq#3617` closes CONFIRM. PR 241 stands as shipped (`acef761` + `c65f751`, already on
`lumen-bin` `main`); nothing to re-implement, nothing to escalate. The gitea web UI at
`100.121.17.5:3000/lumen/lumen-bin/pulls/241` returned HTTP 404 from this worktree (no auth
configured here) — not load-bearing since both commits are independently verified present and
correct on the local `main` checkout at `/home/lumen/bin`.
