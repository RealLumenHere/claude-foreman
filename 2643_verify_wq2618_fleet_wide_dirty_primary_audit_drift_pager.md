# wq#2643 — verify of wq#2618 (PR#1005, merged 015ebdf, main=a35a289)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-sonnet-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2618 claims: `primary_tree_guard.py` had two blind spots closed by wq#1540
(dirty tracked files) and wq#2504 (stale untracked-file stashes) but exposed by
wq#2600 (`/home/lumen/minerva` sat 2 behind / 1 unpushed with uncommitted edits
for 5+ days) — (1) untracked, non-ignored paths (an ops `install.sh` dropping a
file straight into a primary, e.g. minerva's pre-4854d86 `drift_plan.py`) never
paged, since `check_dirty_age` deliberately excludes untracked paths
(`--untracked-files=no`); (2) a primary whose HEAD sits behind
`origin/<default>` never paged either. It adds `check_untracked_age` and
`check_behind_age`, same first-seen/state/edge-clear shape as `check_dirty_age`,
wired into both the CLI `status` and `check` (timer) modes, plus
`/home/lumen/fleet/inventory` to `STATIC_PRIMARY_TREES` (repo_sweep already
covered it for divergence sweeps; this guard's own registry had not). I read the
diff cold against a fresh checkout of `main` (PR#1005 is already merged, commit
`015ebdf`, identical tree to the source branch tip), read every changed line in
`harness/primary_tree_guard.py` and the new/changed tests, ran the merged tree's
test suite, and ran the guard's own `status` probe against the live fleet.

## Per claim

| wq#2618 claim | Result | Evidence |
|---|---|---|
| `check_dirty_age` only ever looked at tracked files, untracked droppings never paged | CONFIRMED | `dirty_files()` (`harness/primary_tree_guard.py:239`) hard-codes `--untracked-files=no`; the new `untracked_files()` (`:301`) uses `git ls-files --others --exclude-standard` instead, a distinct code path, not a flag flip — `--untracked-files=no` is still pinned for the branch-restore safety question by `test_clean_tree_and_untracked_files_are_not_dirty` (`harness/tests/test_primary_tree_guard.py:262`), unchanged by this diff. |
| new `check_untracked_age` pages once >24h, re-pages daily, clears on commit or `.gitignore`, same shape as `check_dirty_age` | CONFIRMED | `harness/primary_tree_guard.py:305-352`; tests `test_untracked_dropping_under_24h_does_not_page`, `test_untracked_dropping_over_24h_pages_once_then_repeats_daily`, `test_gitignored_untracked_dropping_never_pages`, `test_committing_the_dropping_clears_the_episode` (`:360-412`) all pass. |
| new `check_behind_age` pages once >24h behind `origin/<default>`, clears on fast-forward, backdates from HEAD's own commit time | CONFIRMED | `behind_count()`/`_head_commit_time()`/`check_behind_age()` (`:355-425`); tests build a real bare-repo origin + throwaway advancer clone (`make_primary_with_origin`, `advance_origin`, `:418-450`) and assert none/zero/under/over/ff-clear (`:453-500`), all pass. |
| both new checks wired into CLI `status` (report-only) and `check` (timer, alerts + contributes to the Mode-C `still_bad` exit-1 contract) | CONFIRMED | `main()` `:545-559` (status) and `:600-612` (check) call both, printing `UNTRACKED`/`BEHIND` and folding into `still_bad`; `test_cli_check_pages_stale_untracked_and_behind` (`:503-525`) exercises both through the real CLI entrypoint. |
| `/home/lumen/fleet/inventory` added to `STATIC_PRIMARY_TREES`, closing a repo_sweep-vs-guard registry gap | CONFIRMED | `harness/primary_tree_guard.py:121`; `test_primary_trees_default_includes_fleet_inventory` (`:197-202`); live `status` run below shows it resolved (`OK /home/lumen/fleet/inventory: main`), i.e. the path is real, not a dead registry entry. |
| pre-existing behavior (drift restore, dirty-tracked paging, stale-stash reporting) is unchanged | CONFIRMED | `git show 015ebdf -- harness/primary_tree_guard.py` removes exactly 3 lines, all inside the module docstring's exit-code paragraph (superseded by the wq#2618 paragraph) — zero lines of logic touched outside additive new functions/CLI branches. |

## What I tried to break

- Interaction between the untracked and dirty checks on the same file as it
  transitions untracked → staged → committed: staging (`git add`, no commit)
  moves a path out of `untracked_files()` (no longer "others") and into
  `dirty_files()` (staged addition shows in porcelain even with
  `--untracked-files=no`) in the same tick — no double-counting, no gap; not
  separately asserted by the new tests but consistent with both functions'
  independent `git status`/`ls-files` calls.
- Unknown-vs-zero collapse in `check_behind_age`: `behind_count()` returns
  `None` when `origin/<default>` was never fetched locally (explicit, matches
  `repo_sweep`/`repo_reconcile` convention — no fetch from inside the guard
  loop) and `0` when in sync; `check_behind_age`'s `if not n: continue` treats
  both as "don't alert," which is the correct behavior for both cases even
  though the docstring's "unknown is never treated as zero" is about not
  under-reporting drift, not about the skip branch — not a contradiction.
- Systemd wiring: `ops/wq900-primary-tree-guard/primary-tree-guard.service`
  runs `primary_tree_guard.py check` (the timer mode both new checks are wired
  into), confirming this isn't inert code behind a CLI flag nothing invokes.
- Ran `python3 harness/primary_tree_guard.py status` against the live fleet
  (not just throwaway test repos): it correctly flagged real, pre-existing
  staleness — `/home/lumen/kb` (2 dirty tracked, 8 untracked, ~579h) and
  `/home/lumen/mission-control` (4 dirty tracked, ~427h; 16 untracked, ~832h)
  — and reported zero false positives on the clean primaries (`foreman`,
  `bin`, `minerva`, `pokemon`, `questfree`, `pstack`, `mailbot`,
  `fleet/inventory`). No primary is currently `BEHIND`, so that branch is
  untested live, but its throwaway-bare-repo test coverage exercises the real
  git plumbing (`rev-list --count`, `fetch`, `merge --ff-only`), not a mock.
- Ran the full guard-adjacent suite for regressions:
  `harness/tests/test_primary_tree_guard.py` (34/34) and the broader
  `-k "primary_tree or guard"` selection (403 passed, 7 skipped, 1582
  deselected) — no failures, no new skips attributable to this diff.

## Verdict

CONFIRM. No re-implementation performed, per verify-pass doctrine.
