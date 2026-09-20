# wq#1481 — /home/lumen/bin branch reconcile (pre-slice-E fleet-exec)

Verdict: **class fixed, already landed 2026-09-10**; one acceptance item not satisfiable in
this session and split to wq#2186.

## What the ticket found (2026-09-08)

`/home/lumen/bin` checked out on `feat/wq1374-orch-operability-session-budget-blocked-reap-evidence`,
one commit off main, dirty `wq`, 13 untracked scripts. Consequence: workers calling the on-disk
`fleet-exec` got the pre-slice-E broker, so any restart on a non-Lumen node was a polkit refusal.

The premise checks out. `d327b5c` (slice E: privileged restart, Lumen-side holddown, verify
before/after, 2026-09-06) is **not** an ancestor of that branch's tip `2697aac`
(`git merge-base --is-ancestor d327b5c feat/wq1374... ` → false).

## State now (2026-09-20)

- `git -C /home/lumen/bin status -sb` → `## main...origin/main`; `HEAD == origin/main ==
  0145dbf`. No ahead/behind.
- `git merge-base --is-ancestor d327b5c HEAD` → true. The live broker is slice E.
- The wq1374 branch is fully merged: `merge-base --is-ancestor feat/wq1374... main` → true,
  tip `2697aac` included. PR #60 needed no separate landing or archive.
- `/home/lumen/bin/fleet-exec` (22589 b) and `/home/lumen/bin/fleet-exec-restore` (2768 b) both
  exist, mtime 2026-09-10 07:10Z, and neither is dirty. `fleet-exec:55` wires
  `RESTORE_BIN = /home/lumen/bin/fleet-exec-restore`; `:389` registers the Lumen-side holddown
  before the restart, `:418` computes `verified` from a post-restart `is-active`.

Who fixed it: the bin reflog shows `checkout: moving from feat/wq1374-... to main` at
**2026-09-10 07:10:37Z** — the same minute as the fleet-exec mtimes. The dirty-tree half was
then swept by **wq#1987** ([repo-sweep] bin: CONTENT_DIVERGED, done 2026-09-18), which
attributed the `wq` drift to wq#1675's live `--apply` and captured it on
`rescue/wq1987-bin-drift-snapshot` (`9ba6721`, `47cb4da`, merged via `360be56`). No other
lane's work was discarded, and none needed discarding here: `wq` is clean against main today
and carries the landed changes (`7eaa059`, `f8062f2`, `33d5027`, `9948f63`).

## Not satisfied: the live verified=true probe

`/home/lumen/bin/fleet-exec restart isis ollama` from this worker denies:

```
fleet-exec: DENY [grant:grant_missing_env]: grant file did not verify
```

wq#1481 carries no `needs` tag, so `resolve_exec_grant` (`harness/fworker.py:1032`) returned
`(None, None, None)` and no `FOREMAN_GRANT_FILE` was set — correct behaviour, but it makes the
probe unrunnable from this attempt. The deny is itself audited
(`foreman/events/fleet-exec.jsonl`, last row, ticket 1481, `rule: grant:grant_missing_env`).

The only two `verified=true` rows in that audit (lines 13 and 30, both `restart isis ollama`)
were written **2026-09-08 under wq#1441's grant** — while the on-disk broker was still the
wq1374 branch build. They are evidence for slice E as code, not for the binary now on disk.

Split to **wq#2186** (`--needs 'restart(ollama@isis)'`, so the grant exists at spawn): run the
restart, cite the new `decision=allow, verified=true` row with before/after, confirm no
holddown key is left set.

## Unrelated drift noticed, not touched

`/home/lumen/bin` has fresh uncommitted work from other lanes: `lumen-contradiction-scan`
(2026-09-20 16:42Z), `storage-manifest-drift` (09-20 03:40Z), `wq523_pat_residue.py`
(09-20 04:01Z), untracked `minerva-presentation-stability` (09-19 12:01Z). Post-dates wq#1987;
the recurring `[repo-sweep] bin: CONTENT_DIVERGED` auto-ticket is the owner of that class.
