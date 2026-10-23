# wq#4593 verify of wq#4588 — `claude_context.sh --core` payload flaked 74 KB ↔ 115 KB

- Commit: `15eb9456` (merged, cited by ISSUE-LOG row 1559 as PR #2432), re-present on
  this branch as `af301c0c` (`fix(minerva): wq4588 stop tag_sections unlink race
  truncating --core`), Refs wq#4588.
- Author model: claude-opus-5-5. Verifier: claude-sonnet-5@high, fresh instance.
- Verdict: **CONFIRM**. The mechanism, the fix, and the probe are all correct. No
  escalation to opus-5@high.

## What the PR claims

`claude_context.sh --core` raced itself: `context_builder.py` always rewrites
`claude_memory.md`, which fires `minerva-tag-sections.path` → `tag_sections.py`, which
began by unlinking every `sections/*.md` and rewrote them one at a time behind an
Ollama embed call (7–126 s window). `claude_context.sh` runs `context_selector.py`
immediately after the rewrite; `context_selector.read_section()` returns `''` for a
missing file, so any run that landed inside that window silently dropped ~41 KB of
memory sections while keeping an identical-looking header (`selected=8`). 74 KB was
the truncated figure, 115 KB the correct one. Fix (`tag_sections.py`, full
replacement): no up-front unlink; every section file, `sections.json`, and
`claude_memory_index.md` go through `_atomic_write` (tmp + `os.replace`); pruning of
ids absent from the new index happens only after the new index is written. The README
honestly discloses a residual, much narrower race: a reader that loaded the *old*
index just before a prune of a *dropped/renamed* id can still read `''` for that one
id — microseconds, not the original 7–126 s window, and not the mechanism reported in
wq#4585.

## What I checked

- Read the diff cold in full: `README.md`, `tag_sections.py`, `install.sh`,
  `test_tag_sections_race.py`, and both fixtures (`tag_sections.orig.py`,
  `context_selector.py`).
- Confirmed `_split_sections_raw`/`is_core`/`superseded_core_indices`/secret-masking
  logic is untouched — diffed `fixtures/tag_sections.orig.py` against the shipped
  `tag_sections.py` directly (`diff -u`): the only changes are (1) no upfront unlink,
  (2) a new `_atomic_write` helper used for per-section files, `sections.json`, and
  `claude_memory_index.md`, (3) `sec_path` built from the `SECTIONS_DIR` constant
  instead of a second hardcoded live path, (4) the prune loop moved to *after* the new
  index is written and scoped to ids absent from the new index. Minimal, scoped fix —
  no scope creep.
- Confirmed the mechanism against the pinned fixture: `fixtures/tag_sections.orig.py`
  does unconditionally `for p in SECTIONS_DIR.glob("*.md"): p.unlink()` at the top of
  `run()`, and `fixtures/context_selector.py:read_section()` returns `''` when the
  per-section file is absent — exactly the two halves of the claimed race.
- Ran the shipped suite unmodified: `python3 -m pytest -q
  ops/wq4588-core-selector-race/` → **4 passed**, reran the two race-sensitive tests
  individually twice more for flakiness — stable both times (no `errors`, no size
  drift).
- **Falsifiability probe (wq#2630 doctrine)**: a probe that can only confirm is not
  evidence. I drove `_sizes_during_retag()` from the test module directly, once per
  tagger, outside the shipped test functions:
  - Original (buggy) tagger: `at_rest=2095`, min sample `131` B, **776/778** samples
    during the re-tag window landed below at-rest, 0 hard errors. Matches the
    reported shape (a large majority of in-window reads truncated, to a small
    fraction of true size).
  - Fixed tagger run through the *identical* harness: `at_rest=2095`, **0/660**
    samples below at-rest, 0 errors — i.e. the same assertion that fails against the
    original passes against the fix. The probe is genuinely bidirectional, not
    confirm-only by construction.
- Cross-checked `ISSUE-LOG.md` row 1559: narrative, root cause, and fix description
  match the shipped PR exactly; status `fixed-staged`, live deploy tracked separately
  as wq#4592 (`needs: dir(/home/lumen/minerva)`, status `claimed` as of this check —
  not yet landed). My own ticket (wq#4593) carries no `needs: dir(/home/lumen/minerva)`
  tag, consistent with "Verify repo: /home/lumen/foreman" — the live-deploy liveness
  gate is wq#4592's acceptance criterion, not this verify-pass's.
- Attempted the ticket's literal liveness command, `bash
  /home/lumen/foreman/ops/wq4588-core-selector-race/install.sh --verify`, both with
  and without the `bash` prefix: both refused ("This command requires approval").
  `worker-settings.json`'s Bash allowlist only covers `Bash(bash scripts/*)`, not an
  arbitrary absolute path (same root cause already logged at ISSUE-LOG row 53); a
  direct `diff`/`cmp` against the live target (`/home/lumen/minerva/context/
  tag_sections.py`) is separately refused because `/home/lumen/minerva` is outside
  this session's allowed directories — the same wall `install.sh`'s own comment
  documents ("minerva is write-walled from BUILD workers by guard.sh
  WRITE_PRIMARY_TREES"). Both are structural for a BUILD-lane verify ticket, not
  retryable, and not new findings — not logging a new ISSUE-LOG row for either. Live
  deploy state is wq#4592's to establish and verify; nothing here contradicts it.

## Conclusion

The two-line root cause holds up end to end: unlink-before-rewrite plus
read-returns-empty-on-missing-file is sufficient and necessary to produce the
74↔115 KB flake, the fixture-pinned originals reproduce it quantitatively (776/778
truncated samples), and the atomic-write-then-prune-last fix measurably eliminates it
(0/660) under the same harness. The one disclosed residual race (dropped/renamed id,
microsecond window) is correctly scoped and does not contradict the fix's claim. No
contradiction found; confirming on the board.
