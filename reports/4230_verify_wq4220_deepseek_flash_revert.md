# wq#4230 — verify-pass of wq#4220 (deepseek-flash id revert + legacy_aliases + MANIFEST gate)

Archetype 8 adversarial verify-pass, fresh instance, of PR#2148 (merged `2bfe99ba06` in
the upstream PR, landed on `main` as `35dc63d8`). Author: claude-opus-5. Verdict: **CONFIRM**.

## Claims checked against live evidence

1. **`research/models.yaml:234` reverted.** `id: deepseek-v4-flash`,
   `legacy_aliases: [deepseek-flash, deepseek-v4-flash-vision-exp]` — confirmed by direct
   read.

2. **`router._build_aliases` now actually consumes `legacy_aliases`**, ranked below any
   live id or declared `alias`. Exercised live, not just via the shipped unit tests:
   ```
   router.resolve_alias('deepseek-flash')              -> deepseek-v4-flash
   router.resolve_alias('deepseek-v4-flash')            -> deepseek-v4-flash
   router.resolve_alias('deepseek-v4-flash-vision-exp') -> deepseek-v4-flash
   ```

3. **The four hand-maintained pool/tier lists needed no edit**, because they already say
   `deepseek-v4-flash` (never changed by wq#4210/wq#4220 — the revert just made the
   manifest agree with them again):
   `governor/governor_core.py:104`, `orchestrator/judgment.py:42`,
   `orchestrator/router.py:1013`, `despag/tiers.py:37`, plus `despag/harnesses.yaml:35,39`
   (not named in the report but same name, checked anyway). All confirmed via grep.

4. **The despag 422 break wq#4217 found is gone.** Called `despag.tiers.resolve_tier`
   directly (not just via its unit tests):
   ```
   resolve_tier('mechanical', 'deepseek-v4-flash') -> ('chrn_litellm-mechanical', 'deepseek-v4-flash', None, False, None)
   resolve_tier('mechanical', 'deepseek-flash')     -> raises ModelUnavailable (expected: callers must pass the model_name, not the legacy/upstream string, directly into despag)
   ```

5. **`model_ref_lint.py`'s new MANIFEST finding kind fires on the pre-fix shape and is
   clean on the live tree.** Ran the actual probe, not just trusted the committed test:
   ```
   $ python3 ops/wq2903-model-ref-drift/model_ref_lint.py --models-yaml research/models.yaml /home/lumen/bin/model-ref-lint
   model-ref-lint: 0 finding(s); live=35 retired_known=34        # exit 0, post-fix tree

   $ python3 ops/wq2903-model-ref-drift/model_ref_lint.py --models-yaml ops/wq2903-model-ref-drift/tests/fixtures/models_wq4210_drift.yaml /home/lumen/bin/model-ref-lint
   MANIFEST deepseek-flash   .../models_wq4210_drift.yaml:12
   model-ref-lint: 1 finding(s); live=35 retired_known=34        # exit 1, pinned pre-fix fixture
   ```
   This is a probe that can fail — it did fail against the pinned pre-fix fixture and
   passed against the live post-fix tree, on the same binary.

6. **`/home/lumen/bin/model-ref-lint` is byte-identical to the repo copy** — `diff` empty.
   Confirms the resync claim and that the `mistral-small-latest` ALLOW line (added to fix
   the deployed-vs-repo drift wq#4220 noticed) is live in both places.

7. **Test suite: `393 passed`** re-run fresh (`despag/tests orchestrator/tests governor
   ops/wq2903-model-ref-drift/tests`) — matches the claimed count exactly.

8. **ISSUE-LOG #1418** exists and accurately describes cause/fix (`kb/ISSUE-LOG.md:1567`).

9. **Disclosed gap is real, not glossed over.** `lumen-model-ref-lint.timer` is confirmed
   `disabled`/`inactive` (`systemctl is-enabled`/`is-active`), matching wq#4220's own
   "did not touch, needs root" disclosure.

## One discrepancy found — not a functional defect

The report's "full-roots run" paragraph claims **10 findings** (`isis-qwen3-32b` x7,
`mistral-small-latest`, `claude-max-sonnet`, `claude-max-opus`, `lumen-lfm2.5-2.6b`,
`kimik3-0.4b-local`), zero MANIFEST. Re-running the identical no-args invocation now
gives **9 findings**: `isis-qwen3-32b` x5, `claude-max-sonnet` x1, `claude-max-opus` x1,
`lumen-lfm2.5-2.6b` x1, `kimik3-0.4b-local` x1 — `mistral-small-latest` does **not**
appear (correctly: it's in `ALLOW`, which is the point of the line wq#4220 added; its own
report prose listing it as a "finding" is internally inconsistent with the fix it just
shipped). MANIFEST count matches at zero.

This repo is live and shared by many concurrent fleet workers (`git status` shows
unrelated in-flight modifications from other tickets at verify time); the `isis-qwen3-32b`
caller-site count is unrelated RETIRED drift from files wq#4220 never touched, so a count
drift between ticket-time and verify-time is expected environmental noise, not evidence
against this fix. The claim that actually matters to wq#4220/wq#4217 — **zero MANIFEST
noise on a full-roots run** — reproduced exactly. Flagging the inaccurate finding-count
prose for the record; does not change the verdict.

## Verdict: CONFIRM

Every testable, fix-relevant claim reproduced on fresh evidence: the id revert, the real
`legacy_aliases` consumer with correct precedence, the four pool lists needing no edit,
the despag 422 break closed, the MANIFEST gate firing on the exact pre-fix shape and
clean on the live tree, the bin/repo resync, the full 393-test pass, and the disclosed
open gap (timer) all check out independently. No contradiction, no escalation.
