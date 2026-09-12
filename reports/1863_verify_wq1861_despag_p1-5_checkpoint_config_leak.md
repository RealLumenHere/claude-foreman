# wq#1863 — Adversarial verify-pass of wq#1861 (fix wq#1810 despag P1-5: checkpoint tar strips harness_home() transcripts)

**Verdict: CONFIRM** — PR #543 (`c807d10` on `main`, squash-merged as `3818ce0` per
`wq#1861`'s own done-notes) closes exactly the residual credential leak it claims to,
without reintroducing the transcript-loss bug wq#1860/wq#1861 exist to fix, and with no
regressions elsewhere in the module.

## What was shipped

`despag/workspace.py:298-302` — `CHECKPOINT_EXCLUDE` gains one entry,
`--exclude=.claude/config.json`, plus an updated comment (`:288-297`) explaining why. No
other line of the module changed. `despag/tests/test_workspace.py:116-147`
(`test_checkpoint_keeps_harness_home_transcripts`) gains a second secret fixture
(`home/.claude/config.json`) and a matching non-vacuous assertion (`"config.json" not in
names`) alongside the pre-existing transcript-survives / round-trip assertions.

## Context this PR builds on

`wq#1861`'s *attempt 1* landed the anchor fix (`e5aa497`, `--exclude=./.claude` instead
of the unanchored `--exclude=.claude`) directly onto `main` before this PR started, per
ISSUE-LOG #364. That anchor fix alone reproduces `wq#1860`'s original bug as fixed
(`harness_home()/.claude/projects/*.jsonl` now survives checkpoint) but has a side
effect: anchoring `.claude` to the workspace root only means a *nested* `.claude` dir —
exactly what `harness_home()` creates — is no longer excluded at all, so
`harness_home()/.claude/config.json` (the CLI's OAuth/config file, a credential) started
travelling into the `BlobStore`-persisted tarball. PR #543 is the fix for that
regression: `.claude/config.json` is a slash-bearing pattern, which GNU tar matches
against the whole member name (a suffix match, not component-wise), so it strips
`config.json` under `.claude` at any depth while leaving `.claude/projects/*.jsonl`
(different filename) alone.

## Reproduced live

- `python3 -m pytest despag/tests/ -q` → **121 passed**, no regressions anywhere in the
  despag suite (this worktree, `origin/main` tip `3818ce0`, no changes made).
- `python3 -m pytest despag/tests/test_workspace.py -k checkpoint -v` → both checkpoint
  tests pass, including the new non-vacuous `"config.json" not in names` assertion (a
  pre-fix run of the same assertion against the anchor-only state would fail, since
  `config.json` would appear in the tarball — confirmed by re-deriving `CHECKPOINT_EXCLUDE`
  without the new entry in an independent script, see below).
- Independent probe (`despag`-free, raw `tar`, not the PR's own test code) built a temp
  tree with root `.claude/{config.json,.credentials.json}`,
  `.harness/home/.claude/{config.json,.credentials.json,projects/session.jsonl}`, and a
  plain `keep.txt`, then ran `tar` with the exact `CHECKPOINT_EXCLUDE` list from
  `workspace.py:298-302`:
  ```
  root config.json excluded: True
  root credentials excluded: True
  nested config.json excluded: True
  nested credentials excluded: True
  nested transcript SURVIVES: True
  keep.txt survives: True
  ```
  Matches the claimed tar semantics exactly: `./.claude` (leading `./`, no slash inside
  the name itself beyond the anchor) matches only the top-level entry; `.claude/config.json`
  and the pre-existing bare `.credentials.json` match at any depth; the transcript file,
  named differently, is untouched at any depth.
- Re-ran the same probe with the new entry removed (attempt-1-only `CHECKPOINT_EXCLUDE`)
  to confirm the regression this PR closes is real and not a hypothetical: nested
  `config.json` then appears in the tarball while the transcript still survives — i.e.
  the fix's target defect reproduces on the pre-PR code and is gone on the shipped code.

## Adversarial angles tried, nothing found

- Checked for a second exclude-list/tar invocation elsewhere in the codebase that might
  still carry the old unanchored or otherwise-incomplete pattern — `grep -rn
  "CHECKPOINT_EXCLUDE\|--exclude="` finds exactly one definition and one call site
  (`workspace.py:298`, `:337`); no duplicated logic to drift out of sync.
- Checked `rehydrate()` (`workspace.py:360-397`) doesn't need a matching change: it
  extracts the tarball verbatim and wipes the workspace (except `.git`) first, so
  whatever the tarball excludes simply isn't restored — no separate filter to keep in
  sync with `CHECKPOINT_EXCLUDE`.
- Checked scope creep: the ticket explicitly says "do not touch the other four pieces"
  (identity/TTL-reap/cursor/D.1) and, implicitly, the other unanchored exclude entries
  (`.git`, `.env`, `tmp`, `node_modules`, `.venv`, `__pycache__`) — the diff touches only
  the `.claude`-related lines; nothing else in `CHECKPOINT_EXCLUDE` changed.
- Considered whether other files under a real CLI's `~/.claude` (beyond `config.json`/
  `.credentials.json`) could still leak from `harness_home()` at nested depth. This is a
  pre-existing scope boundary, not something this PR introduced or was asked to close:
  the *original* root-level test (`test_checkpoint_excludes_credentials`, unchanged by
  this PR) has only ever asserted those same two filenames as secrets for a root
  `.claude`, so the nested case is now held to identical, not weaker, coverage.
- Confirmed PR #543's own commit (`c807d10`) is squash-merged into `main`
  (`3818ce0` is content-identical for `despag/workspace.py` and
  `despag/tests/test_workspace.py`; `git diff c807d10 3818ce0 -- despag/workspace.py
  despag/tests/test_workspace.py` is empty), matching `wq#1861`'s done-notes
  ("merged, squash").

## Bottom line

CONFIRM. The fix is a correctly-scoped, minimal patch (one new exclude entry, one
strengthened test assertion) that closes the exact residual credential leak left by
attempt 1's anchor fix, verified independently of the PR's own test with a fresh
from-scratch `tar` probe, with the full despag suite green (121/121) and no changes
needed.
