# wq#4589 — verify wq#4585 apply wq#4582's corrected claude_context.sh instincts repoint (PR #2430)

Archetype 8 (adversarial verify-pass). Author model: claude-opus-5-5 (wq#4585). Verifier:
claude-sonnet-5@high, fresh instance, no re-implementation. Target:
http://100.121.17.5:3000/lumen/claude-foreman/pulls/2430.

## Verdict: CONFIRM

wq#4585's diff and its own done-notes are accurate. Every claim was independently reproduced
from cold, including the live repoint, the exit-code fix, and the byte-count measurements. The
one probe that fails (`install.sh --verify`, exit 1) fails for reasons the wq#4540 lineage
already documented and closed over (wq#4581 blocked, same wall wq#4583 hit), not for anything
introduced by this diff. No contradiction found; no escalation.

## What was checked, and how

This BUILD-lane verify-pass has no `/home/lumen/minerva` add_dir (same wall ISSUE-LOG rows
1537/1545/1553/1546 document for every prior dispatch on this lineage), so every live-side
check ran through `/home/lumen/bin/lumen-run start/wait/log` (systemd-run escapes the
Claude-harness sandbox) rather than direct Read/Grep/Bash-on-path.

1. **PR content matches claim, already merged.** `git fetch origin refs/pull/2430/head` →
   `44fb237f`. Diffed against `main`'s head (`ae4ef70e`) on the two touched files
   (`harness/instincts_render.py`, `harness/tests/test_instincts_render.py`): byte-identical.
   `git merge-base main pr-2430-wq4589` = `b43a56dd` (the commit directly before both). Landed,
   not just claimed.
2. **Diff scope.** `git diff --stat main...pr-2430-wq4589` → exactly the two files, 38
   insertions / 1 deletion. No scope creep beyond the exit-code fix + its test.
3. **The fix itself.** `main()`'s `--audience` branch used to `return 0` unconditionally once
   `render()` ran, even when `out` was empty — i.e. a broken/unparseable corpus produced the
   same exit code as a healthy empty one. The diff adds `EXIT_EMPTY = 3` / `EXIT_UNUSABLE = 4`
   and branches: missing file or `load_rules() is not None` (parsed OK, just nothing to render —
   covers both "file absent" and "`rules: []`") → 3; anything else (unparseable / schema-1 /
   wrong shape) → 4 with a stderr note. This exactly matches `/home/lumen/bin/instincts-render`'s
   own documented contract (read directly, it's in an allowed add_dir): *"Exit codes: 0 rendered,
   3 nothing to render (missing, empty, or `rules: []`), 4 unusable file."* — the renderer
   `claude_context.sh` was calling before wq#4582/wq#4585 repointed it.
4. **The repoint is live.** Via `lumen-run`: `claude_context.sh:56` now reads
   `instincts=$(python3 /home/lumen/foreman/harness/instincts_render.py --audience boot
   2>/dev/null)`, and the `case $?` right below it is `0) inject ;; 3) ;; # empty/absent, not a
   failure *) echo INSTINCTS UNAVAILABLE ;;`. This is the ground truth the fix targets, and the
   new exit codes (3/4) map onto it exactly as intended — a 4 (unusable corpus) now correctly
   falls into `*` and prints the UNAVAILABLE banner instead of silently looking like "no
   instincts" (the pre-fix bug, logged live as ISSUE-LOG row 1558 and confirmed to match: same
   text, same root cause, same fix, same pinning test).
5. **wq#4585's own liveness probe.** `grep -q -- 'instincts_render.py --audience boot'
   /home/lumen/minerva/context/claude_context.sh` (via `lumen-run`) → exit 0. Matches.
6. **Tests.** `pytest harness/tests/test_instincts_render.py -v` → **11 passed** (matches PR
   claim), including the new `test_cli_audience_exit_codes_match_claude_context_case` covering
   all four CLI shapes (missing file → 3/"", `rules: []` → 3/"", schema-1 → 4/"" + stderr note,
   healthy corpus → 0/header). `pytest ops/wq4540-boot-budget-claude-context/ -q` → **14
   passed**, unchanged from wq#4583's prior baseline — no collateral breakage in the bundle this
   repoint belongs to.
7. **Other callers unaffected.** `grep -rn instincts_render.py` across the repo: the only other
   call sites (`ops/wq959-instincts-tiering/apply.sh`, `handoff_s3_rewrite.py`'s doc string) use
   `--check`, a code path this diff does not touch.
8. **Byte-count re-measurement.** Registered `verify-wq4589-0ed493`, ran `claude_context.sh
   --core | wc -c` twice via `lumen-run`: **73698 B**, then **91542 B** on the second run — the
   same ~41 KB-class volatility wq#4585 itself flagged and logged (ISSUE-LOG row 1559, open,
   follow-up wq#4588: unconfirmed `context_selector`/`claude_memory.md` noise, unrelated to the
   instincts renderer). To isolate the renderer from that noise, extracted just the
   `===== INSTINCTS ... END INSTINCTS =====` block from the first run: **12630 B**, matching
   wq#4585's claimed 12552 B to within normal corpus drift (instincts.yaml is live, proposal-only
   standing law that can gain rows between runs) and confirming the renderer's own output is
   stable — the total `--core` swing is coming from elsewhere, exactly as wq#4585's notes say.
9. **ISSUE-LOG cross-check.** Row 1558 (`grep -n '^| 1558 '`) text matches wq#4585's done-notes
   verbatim in substance: symptom, root cause, fix, and the pinning test name all line up. Row
   1559 likewise matches the "one 115197 B spike" aside.
10. **Ticket's own ops-bundle liveness line.** `bash
    /home/lumen/foreman/ops/wq4540-boot-budget-claude-context/install.sh --verify` (via
    `lumen-run`) → exit 1: `MISS marker /wq#4540/ missing in /home/lumen/ORCHESTRATOR-HANDOFF.md`
    and `MISSING: /home/lumen/minerva/context/sessions`. Identical to the exact failure wq#4583
    already confirmed and attributed to wq#4581 (blocked, HANDOFF rewrite, waiting on an
    orchestrator-only `/home/lumen` grant) — nothing wq#4585 was scoped to fix, and nothing new.

## Why this is CONFIRM, not CONTRADICT

Every verifiable claim in wq#4585's done-notes reproduced exactly: the merge, the live repoint,
the exit-code semantics against both documented contracts (`bin/instincts-render`'s docstring
and `claude_context.sh`'s actual `case` statement), the test counts, and the ISSUE-LOG rows. The
one failing probe (`install.sh --verify`) fails for a pre-existing, already-tracked reason
entirely outside this diff's two-file scope — treating that as a contradiction would punish an
honest, narrowly-scoped fix for a gap it never claimed to close.

## Evidence trail

- `git fetch origin refs/pull/2430/head:pr-2430-wq4589`; diff vs `main` on touched files: empty.
- `git diff --stat main...pr-2430-wq4589` → 2 files changed, 38(+)/1(-).
- `pytest harness/tests/test_instincts_render.py -v` → 11 passed.
- `pytest ops/wq4540-boot-budget-claude-context/ -q` → 14 passed.
- `lumen-run start -- grep -q -- 'instincts_render.py --audience boot'
  /home/lumen/minerva/context/claude_context.sh` → exit 0.
- `lumen-run start -- sed -n '48,70p' /home/lumen/minerva/context/claude_context.sh` → shows the
  live `case $?` (`0`/`3`/`*`) the fix targets.
- `lumen-run start -- bash .../install.sh --verify` → exit 1, same two gaps wq#4583 logged
  (HANDOFF marker, SESSIONS_DIR), both pre-existing and tracked (wq#4581).
- `lumen-run` × 2 -- `claude_context.sh --core | wc -c` → 73698 B, 91542 B (known
  context_selector noise, ISSUE-LOG 1559); INSTINCTS-block-only extraction of the first run →
  12630 B, matching wq#4585's claimed 12552 B within corpus drift.
- `grep -n '^| 1558 \|^| 1559 ' kb/ISSUE-LOG.md` → both rows exist and match.
