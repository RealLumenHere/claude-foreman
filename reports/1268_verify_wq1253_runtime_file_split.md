# wq#1268 — verify-pass: wq#1253 hardening 1/9: runtime-file split + gitignore

**Verdict: CONTRADICT (operational)** — the shipped diff is correct and its own test
suite proves the property it claims in isolation, but the fix is not live on the fleet's
actual primary tree, and the exact bug it was filed to close (dirty tracked runtime state
false-blocking `lumen-pr land` into foreman) reproduced live during this verify, minutes
after wq#1253 marked itself done, via the guard's own real check, not a re-implementation.

PR: `lumen/claude-foreman` #196, commit `85227be7579d7f45ac03cbb6b8f4c0f17f64b7f3`
(`Refs: wq#1253`), 9th commit back from `origin/main` HEAD `897ff1d`. Confirmed on
`origin/main` (local `main` was stale and lacked it — had to `git fetch origin main`
first).

## What was verified and holds up

- `python3 -m pytest dispatcher/tests/test_runtime_state_split.py -q` → **7/7 pass**,
  run against a detached checkout of the actual merged `origin/main` commit.
- Full suite: `python3 -m pytest dispatcher/tests/ -q` → **169/169 pass** (81.15s),
  matching wq#1253's own done-note count exactly.
- `dispatch_health.py`/`dispatch_config_lint.py` diffs: `STATE_PATH` now resolves under
  `dispatcher/runtime/`, `_LEGACY_STATE_PATH` keeps the old tracked path, `load_state()`
  prefers the new path once it exists and falls back to legacy otherwise, `save_state()`
  creates `runtime/` on first write. All four combinations (new-only, legacy-only,
  both-present, neither) are covered by the shipped tests and pass.
- `.gitignore` additions (`runtime/`, `*.err`, `.*.err`) are repo-root patterns, so they
  match a directory/file named that anywhere in the tree, not just under `dispatcher/` —
  checked for collateral damage: `git ls-tree -r HEAD --name-only | grep -E
  "(^|/)runtime/"` and `grep -E "\.err$"` both return nothing outside
  `dispatcher/runtime/` itself. No other tracked path is shadowed.
- `dispatcher/config.json` is untouched (still tracked, unaffected by the split, per
  wq#772) — confirmed by the diff stat (no changes to that file).
- `dispatcher/DESIGN.md` documents the new paths and the one-release fallback
  accurately; matches the code.

## Contradiction — the false-block is reproducing live on primary, right now

The ticket's own acceptance test (ISSUE-SOLUTIONS-fable.md #23): *"run the harness
(dirtying lane/health state), then `lumen-pr` land into foreman → guard passes clean, no
override. Signal: zero `LUMEN_PR_ALLOW_DIRTY` occurrences in logs."*

Ran the read-only form of that exact check — `lumen-pr dirtycheck` exercises the real
`dirty_target_guard()` function `lumen-pr land` would use, no re-implementation:

```
$ /home/lumen/bin/lumen-pr dirtycheck /home/lumen/foreman --mode warn dispatcher/health_state.json
lumen-pr: TARGET TREE HAS WORK ORIGIN DOES NOT (wq#771/wq#792)
  target tree: /home/lumen/foreman
  1 path(s) this path list touches are affected:
  uncommitted there:
    dispatcher/health_state.json
lumen-pr: WARNING: if this branch lands while that stays uncommitted/un-pushed, land will refuse (wq#771/wq#792)
```

This is not hypothetical — it is the state of the actual primary tree at verify time:

- `git -C /home/lumen/foreman log --oneline -5` shows local `main` at `44160ef`, which
  predates `85227be` — **primary has not deployed wq#1253's fix.**
- `git -C /home/lumen/foreman status --porcelain` shows `M dispatcher/health_state.json`
  — the exact tracked-file dirt class #23 was filed to eliminate.
- Content proves *why*: the tracked file's committed content is timestamped
  `2026-08-27T22:30:48Z`; the on-disk (dirty) content is `2026-09-02T11:31:36Z` — **49
  seconds after wq#1253's own `done_at` (11:30:47)**. `dispatcher/runtime/health_state.json`
  exists but is stale (`2026-09-02T11:11:07Z`), proving the daemon currently running on
  primary (`lumen-foreman-health.timer`, every 5 min) is still executing the pre-fix
  code and writing the old tracked path — it has not picked up the new code at all.
- There is no mechanism that would have advanced primary past this: `cmd_land`'s
  `git merge --ff-only origin/$DEFAULT_BRANCH` (`lumen-pr:1031`) only runs against the
  land command's own `$REPO_PATH` (the landing worker's own worktree), not primary,
  unless the land is invoked directly from primary itself. `harness/primary_tree_guard.py`
  (the 5-minute drift-check timer, wq#900) only asserts primary stays on its default
  branch and auto-restores branch drift — `grep -n "ff-only\|merge\b" harness/
  primary_tree_guard.py` returns nothing; it does not fast-forward primary to new
  commits on `origin/main`. Nothing in this repo auto-deploys a merged PR to the
  primary tree that actually runs the daemons.

wq#1253's own done-notes claim "PR landed clean, no `LUMEN_PR_ALLOW_DIRTY` override" —
true for the instant of landing (the worker pre-migrated primary's dirty file and
restored it to `HEAD` specifically to pass the guard at that moment), but the tree did
not stay clean: the still-undeployed daemon re-dirtied it within the same minute, and it
has stayed dirty since. Read as "the false-block problem is solved," which is how the
ticket's own acceptance signal ("zero `LUMEN_PR_ALLOW_DIRTY` occurrences") is phrased,
that claim does not hold right now. Anyone landing a second PR into foreman today would
hit this guard and be one `LUMEN_PR_ALLOW_DIRTY=1` away from reintroducing the exact
anti-pattern #23 was written to retire.

## Disposition

Code: correct, matches the spec, fully tested, no collateral gitignore damage — **confirmed**.
Deployment: primary `/home/lumen/foreman` never received the fix and has no automated
path to receive it — **contradicts the acceptance signal, live, right now**. Not fixing
this myself: primary also carries unrelated uncommitted work from other lanes
(`plans/wq1270.md`, `plans/wq997-dispatch-deadman.unit.bak-*`) that a verify-pass has no
business reconciling, and "do not re-implement" extends to not improvising a primary-tree
migration+deploy under this ticket. Escalating to opus-5@high per ticket instructions —
recommend a follow-up ticket to (1) do the one-time reconcile+`ff-only` deploy on
primary now, and (2) decide whether landed hardening/runtime-path changes need an
explicit deploy step or auto-sync, since every future ticket in this same 1/9 series
will hit the identical gap otherwise.
