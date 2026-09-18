# wq#1980 — verify-pass: wq#1979 fix 3 dangling `foreman/research/` doc links (PR#609)

**Verdict: CONFIRM** — the diff fixes exactly the 3 dangling references flagged by the
wq#1978 verify-pass (`reports/1978_verify_wq1976_router_docs_relocate.md`), nothing more
and nothing less, and the repo-wide probe for all 5 relocated-doc old-paths now returns
zero live hits.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/609 (author commit `89e22a3`,
merged into main as merge commit `bd7cfce`). Diff read cold via `git fetch origin
refs/pull/609/head:pr609` and `git show 89e22a3`, not the PR's own description of itself.

## What the diff does

Three one-line edits, each dropping a stray `foreman/` prefix off a relative doc path that
used to be correct (pre wq#1976) and went dangling when wq#1976/PR605 moved the target docs
from `foreman/research/*.md` to `research/*.md`:

1. `ops/wq1573-glm53flash-eval/README.md:3` — `foreman/research/GLM53_FLASH_EVAL.md` →
   `research/GLM53_FLASH_EVAL.md`
2. `research/ALERT_TRIAGE_LLM.md:272` — `foreman/research/GLM53_FLASH_EVAL.md:8` →
   `research/GLM53_FLASH_EVAL.md:8`
3. `research/GLM53_FLASH_EVAL.md:42` — `foreman/research/LPP_BRAIN_AB.md` →
   `research/LPP_BRAIN_AB.md`

This is a literal 1:1 match to the 3 findings listed in wq#1978's report (lines 65-67 of
`reports/1978_verify_wq1976_router_docs_relocate.md`), which is the ticket that spawned
wq#1979.

## Verification performed

1. **Re-ran wq#1978's own probe against current main** (post-merge): `grep -rln
   "foreman/research/GLM53_FLASH_EVAL\.md\|foreman/research/LPP_BRAIN_AB\.md\|
   foreman/research/VENDOR_KEY_ROTATION\.md\|foreman/research/VLOGS_PURGE_WQ1747\.md\|
   foreman/research/brain/pragmatics\.md"` across the whole repo → only hits left are under
   `reports/` (historical narrative of past verify-passes, e.g. `1972_verify_...`,
   `1974_verify_...`, `1978_verify_...`, plus two older `1426`/`1427` reports), which
   wq#1978 itself established are out of scope (dead-narrative, not live pointers). Zero
   hits in live docs — confirmed directly with `grep -n "foreman/research"` scoped to the
   3 touched files, exit 1 (no match).
2. **Confirmed each new target path resolves**: `research/GLM53_FLASH_EVAL.md`,
   `research/LPP_BRAIN_AB.md`, `research/ALERT_TRIAGE_LLM.md` all exist at repo root on
   current main; `foreman/` is not a directory in this repo at all (`ls foreman` →
   ENOENT), consistent with wq#1978's finding that `FOREMAN_ROOT` already points at this
   repo's own root, so any `foreman/`-prefixed relative path was always double-nested.
3. **Checked for scope creep / missed cases**: diffed the 3 changed files' full content
   against their wq#1978-report-cited line numbers — no unrelated edits, no files touched
   beyond the 3 named. Confirmed the count in the ticket title ("3 dangling ... links") is
   exact, not rounded.
4. **Noted but out of scope**: `pilots/a7d04e5ebe7d/CARD.md:10` and
   `pilots/78590aee9360/REPORT.md:11` also reference a dangling
   `foreman/research/DEFER_PILOT_LANE.md` (real file lives at
   `research/DEFER_PILOT_LANE.md`). This is a pre-existing, unrelated dangling link —
   `DEFER_PILOT_LANE.md` was never one of the 5 docs wq#1976 relocated, so it isn't a
   regression this PR chain caused and isn't covered by wq#1979's scope ("fix 3 dangling
   ... links" — this would be a 4th, different one). Not a contradiction of wq#1979's work;
   flagging for a separate ticket if wanted, not filing one myself (verify-only mandate).
5. **Confirmed merge integrity**: `bd7cfce` is a clean 2-parent merge of `89e22a3` into the
   `7e1ea53` tip, `git rev-parse HEAD origin/main` both resolve to `bd7cfce` in this
   worktree — PR is actually merged, not just open.

## Conclusion

CONFIRM. wq#1979's fix is correct, minimal, and complete for its stated scope. No
regression, no scope creep, probe passes clean. Did not re-implement — verify only.
