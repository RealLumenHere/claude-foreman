# wq#3358 — verify-pass of wq#3353 (archive_move.sh non-empty-diff-as-OK), PR #1558

Archetype 8 adversarial verify. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model). Result: **CONFIRMED** — no contradiction, no escalation.

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1558, branch
`feat/wq3353-archive-move-sh-reports-non-empty-diff-a-a2` (commit `9541f38d`), touches one
file: `ops/wq3304-archive-gocheckin-samsung-migration/archive_move.sh` (65 lines, shown as
"new" because the file's parent commit `bb8d4038` lives only on the still-unmerged
`chore/wq3304-archive-gocheckin-20260811-samsung-migra-a2` branch — confirmed via
`git merge-base --is-ancestor bb8d4038 origin/main` → exit 1 — so diffing against `main`
is misleading; the real fix is one line, visible via `git diff bb8d4038 9541f38d`).

## The actual fix

```diff
-      diff_out="$(rsync -rc --dry-run --delete "$SRC_ROOT/$d/" "$DST_ROOT/$d/")"
+      diff_out="$(rsync -rci --dry-run --delete "$SRC_ROOT/$d/" "$DST_ROOT/$d/")"
```
(`archive_move.sh:34`, plus an explanatory comment). Everything else — the `[ -n
"$diff_out" ]` gate, the independent sha256-manifest check, `fail` aggregation, `exit
"$fail"` — is unchanged.

## What was checked

1. **Root-cause claim: plain `rsync -rc --dry-run` (no `-v`/`-i`) prints nothing on
   stdout even when there is a real diff, so `diff_out` was always empty and the branch
   always printed "VERIFY OK."** Could not execute `rsync` directly — it is not on this
   worker's Bash allowlist (`harness/worker-settings.json`), confirmed by `rsync --dry-run
   ...` → `This command requires approval` on every variant tried, including with
   `--version` alone. This is a real allowlist gap, not a retryable fluke — flagging per
   the ticket's rules (see below). The wq#3353 author hit the identical wall: their event
   log (`events/fworker-wq3353-a2.jsonl`) shows an attempted `rsync` probe against a
   synthetic src/dst pair followed immediately by `man rsync > ...; grep -c itemize ...`
   — the same fallback I independently reached before seeing their log. `man` is
   allowlisted; I used it directly: `man rsync | sed -n '3077,3095p'` →
   > `-i, --itemize-changes` ... "Requests a simple itemized list of the changes that are
   > being made to each file" ... and the `--dry-run` entry: "most commonly used in
   > combination with --verbose (-v) and/or --itemize-changes (-i) **to see what an rsync
   > command is going to do**" — i.e. without one of those two flags, dry-run gives no
   > per-file signal. This is the local, authoritative primary source for the bug claim;
   > it checks out exactly as stated.
2. **Fix claim: adding `-i` makes `diff_out` non-empty iff there's a real difference,
   and empty iff synced.** Per the same man-page section, a single `-i` emits a line
   per changed/missing/extra file and (per the "if you repeat the option" caveat)
   explicitly does *not* emit lines for unchanged files unless `-i` is given twice —
   it's given once here, so the empty-means-synced invariant the `[ -n "$diff_out" ]`
   check relies on holds.
3. **Cross-check against a script that actually ran for real.** `/home/lumen/scratch/
   wq3304_verify.sh` — the reference verifier wq#3353's own ticket title points at, and
   which wq#3304's done-notes confirm was the actual tool used for the real
   gocheckin/samsung-migration copy+verify (archive_move.sh was explicitly *not* used for
   that run, per wq#3304 notes: "its ... zero-diff test can false-OK on a non-empty
   diff") — uses the identical technique: `rsync -rc --dry-run --itemize-changes ... >
   file`, then `wc -l` the file and fail if non-zero. Independent confirmation that
   `--itemize-changes` is the correct, already-production-validated fix shape for this
   exact check.
4. **Scope/regression check.** No other file in the diff; no change to the sha256
   fallback check, `fail` aggregation, or the `delete` command's own re-invocation of
   `verify` as a guard. Itemize-changes' attribute columns (perm/owner/group/time) only
   populate when those attributes are part of the transfer (`-p`/`-o`/`-g`/`-t`), none of
   which are passed here, so `-i` adds no permission-only false-positive noise — only
   content (via `-c` checksum), missing, and `--delete`-flagged extra files surface.
   `set -euo pipefail` interaction is unchanged (dry-run with real diffs still exits 0;
   `-e` was already live on the pre-fix line).

## One scope note, doesn't change the verdict

The sha256 manifest check two lines below is an independent, already-correct safety net
for content mismatches and extra/missing *files* (it hashes `find -type f` listings on
both sides, so a changed or extraneous file changes the aggregate hash regardless of the
rsync bug). The practical blast radius of the original bug was therefore "misleading log
line" more than "silent data-loss," for the cases that check covers. Neither check
(pre- or post-fix) covers non-regular-file divergences (empty dirs, symlink differences) —
a pre-existing gap outside wq#3353's stated scope (it targeted the rsync check
specifically), not introduced or worsened by this diff.

## Allowlist gap to flag

`rsync` is not on `harness/worker-settings.json`'s per-command allowlist at all (no
`Bash(rsync *)` entry), for either build or verify workers. Both this ticket's author and
I independently needed it to empirically probe an rsync-flag bug and both had to fall back
to `man rsync` instead of a live repro. Worth adding a scoped `Bash(rsync --dry-run *)` or
similar read-only allowance so future rsync-behavior tickets can be probed directly rather
than argued from documentation.

## Verdict

CONFIRMED on the board. The bug claim matches rsync's own man page exactly; the fix
(`-rc` → `-rci`) is the minimal correct change, matches the technique already proven in
production by `/home/lumen/scratch/wq3304_verify.sh`, and introduces no scope creep or
regression. No re-implementation performed.
