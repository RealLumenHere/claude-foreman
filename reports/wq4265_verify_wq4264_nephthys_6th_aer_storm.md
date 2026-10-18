# wq#4265 — verify-pass of wq#4264 (selfheal.yaml nodes.notes.nephthys)

**Verdict: CONFIRM.**

Verifier: claude-sonnet-5@high, fresh instance (same model as author,
claude-sonnet-5). Read the PR cold (lumen/claude-foreman#2181, merged as
`167ea1192a` on the author's branch, squash-landed on `main` as `cffc33f5`
— byte-identical diff content, only the commit hash changed, which is
expected Gitea squash-merge behavior, not a discrepancy). Did not
re-implement.

## What wq#4264 shipped

A one-line edit to `ops/wq3354-fleet-selfheal/selfheal.yaml`'s
`nodes.notes.nephthys` string: bumped "5x so far" → "6x so far", appended
"a 6th 2026-10-18 as wq#4264" to the occurrence list, and rewrote the
closing clause from "each occurrence gets its own ... physical ticket" to
"wq#3976 ... opened for the 5th occurrence is STILL OPEN and unclaimed as
of the 6th ... so wq#4264 deps-links onto the existing wq#3976 rather than
filing a duplicate physical ticket." `git show 167ea1192a --stat`: 1 file
changed, 1 insertion, 1 deletion. No other path touched.

## What I checked

- **wq#4264's own done-notes** ("updated nodes.notes.nephthys ...
  deps-linked onto still-open wq#3976 instead of filing a duplicate
  physical ticket") match the diff and `wq show 4264`'s `deps: 3976`
  field verbatim.
- **wq#3976 live status**: `wq show 3976` → `status: open`,
  `claimed_by: None` — confirms the note's central factual claim ("STILL
  OPEN and unclaimed as of the 6th") is true right now, not just asserted.
  This is the one claim that could have been false (if Frank had already
  cycled the plug or someone had claimed the ticket) and it checks out.
- **No duplicate physical ticket**: `wq list --lane frank-hw | grep -i
  nephthys` shows only wq#3170 (old, unrelated), wq#3976 (open, the one
  reused) and wq#3316 (done, the 4th-occurrence ticket). No new
  `[physical] nephthys` ticket was filed for the 6th occurrence — the
  dedup claim holds.
- **nodes.ignore / nodes.sleepers**: parsed the live YAML
  (`python3 -I -c 'yaml.safe_load(...)'`) — `nephthys` is in neither list.
  The note's explicit "do NOT add nephthys to nodes.ignore" instruction
  was honored.
- **YAML validity**: file parses cleanly; `nodes.notes.nephthys` round-trips
  to the expected 930-char string containing "6x so far", "wq#4264", and
  "wq#3976".
- **Blast radius**: `nodes.notes.<node>` has no other consumer in the repo
  (`grep -rl "nodes.notes\|selfheal.yaml" --include=*.py` only hits the
  defining module); `sh_nodes.py:185-187` just folds the string verbatim
  into the next outage ticket's body via `f"known issue: {note}"` — no
  length limit, no character restriction, so this is a pure documentation
  change with no code-path risk.
- **Live probe**: `fleet-ssh-check --nodes nephthys --quiet` still exits 1
  (nephthys is still offline) — correctly, since wq#4264 never claimed to
  fix the hardware, only to document/dedupe the paging. Nothing in the
  diff or done-notes overclaims resolution.

## Why this could have failed but didn't

The verify-pass doctrine requires a probe able to fail. The one here was
wq#3976's live status — had Frank already cycled the plug or claimed the
ticket, the new note's "STILL OPEN and unclaimed" claim would be stale/
false and the dedup logic would be wrong (a new physical ticket would be
needed). It isn't: `status: open`, `claimed_by: None`, confirmed live.

No contradiction found. No escalation warranted.
