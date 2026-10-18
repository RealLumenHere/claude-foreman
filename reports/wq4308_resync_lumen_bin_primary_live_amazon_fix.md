# wq#4308 — resync lumen-bin primary tree to main (wq#4281 fix now live)

## Before

- `/home/lumen/bin` checked out `feat/wq4281-strip-amazon-subscriptionid`, 0 ahead /
  2 behind `origin/main` (merge-base `5d289ea` == branch HEAD). `a3fef0e` and
  `f28c22f` were on `main` but not on the live checkout.
- `git -C /home/lumen/bin merge-base --is-ancestor a3fef0e HEAD` → non-zero.
- Tree was dirty: 8 modified tracked files (`gitleaks-detect-cron`,
  `litellm-auth-alert`, `litellm-completion-probe`, `litellm_rungs.py`,
  `model-ref-lint`, `sentinel-watch`, `ui-inventory`, `wake-dispatch.sh`) plus 5
  untracked files (`aider-fworker`, `fleet-auth-browser`, `mac-bench.py`,
  `mac-bench.sh`, `obsidian-sync`). `aider-fworker` was byte-identical to the copy
  `f28c22f` tracks.

## What landed

- Backed up the 8 dirty tracked files and moved the 4 non-`aider-fworker`
  untracked files aside, discarded the tracked diffs via the guard-sanctioned
  `git checkout -- <relpaths>` carve-out (wq#1335), leaving the tree clean.
- Primary advanced to `main` at `f28c22f` (via the fast-forward that
  `lumen-pr land` below also performs for a primary registered with it).
- Found PR #258 (`chore/wq4308-preserve-live-bin-edits`, opened by an earlier
  attempt on this same ticket, in a dedicated worktree at
  `/tmp/fworker-wq4308/lumen-bin-a1`) already carried a commit (`3c401fa`) with
  all 12 of the preserved files byte-identical to what was backed up here.
  Landed it: `/home/lumen/bin/lumen-pr land /tmp/fworker-wq4308/lumen-bin-a1 258`.
  `lumen-pr` squash-merged it as `3c401fac82` and fast-forwarded the primary to
  `fc62d28` in the same call (no `sync-live` invocation needed — see
  ISSUE-LOG row 1450 for why `sync-live` itself was not directly usable here).

## After (evidence)

- `git -C /home/lumen/bin log --oneline -3` → `fc62d28` (preserve commit) →
  `f28c22f` → `a3fef0e`.
- `git -C /home/lumen/bin merge-base --is-ancestor a3fef0e HEAD` → zero (is an
  ancestor).
- `git -C /home/lumen/bin status --porcelain` → empty.
- The implementing code for the Amazon strip lives at
  `/home/lumen/bin/fleet-search:273` (`_clean_amazon_url`) and
  `/home/lumen/bin/fleet-search:287` (`_clean_amazon_noise`), both now reachable
  on the primary's live `HEAD`.
- `grep -c _clean_amazon /home/lumen/bin/fleet-search` → **10**, not the 6 this
  ticket's own body quoted. That "6" was already flagged as stale by
  `reports/wq4305_judgment_wq4281_fleet_search_amazon_scope.md` §1 ("the ticket's
  own liveness line claims 6; that claim describes `main`'s blob, not the live
  file") — re-deriving it independently here against the actual landed blob
  gives 10 matching lines (`grep -n` lines 273, 287, 288, 291, 327, 328, 353,
  354, 400, 401), and 13 if the original combined pattern
  `_clean_amazon\|AMAZON_NOISE_PARAMS` is used instead. The number the ticket
  quoted does not match either reading of the live file at any point in this
  repo's history checked; the ancestry and dirty-tree reconciliation are the
  acceptance-bearing facts and both hold.
- All 8 originally-dirty tracked files and all 4 non-`aider-fworker` untracked
  files are preserved via the landed commit `3c401fac82` (not discarded);
  `aider-fworker` is preserved as the tracked copy `f28c22f` already carried
  (byte-identical to the untracked copy that was on the primary before this
  ticket).

## Issues hit

ISSUE-LOG row 1450: `sync-live` (the guard-hook-prescribed resync tool) is not on
the worker Bash allowlist, and the `git checkout -- <paths>` carve-out in
`guard.sh` only matches a Bash call whose entire raw text is that one line (a
multi-line call with trailing `echo`/status commands defeats the anchor since
newline isn't a segment terminator there). Both detailed in the row, with a
workaround (route through `lumen-pr land`, which calls ff-primary itself) and a
suggested guard/allowlist fix.
