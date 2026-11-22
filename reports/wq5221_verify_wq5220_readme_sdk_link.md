# wq#5221 — verify wq#5220 (harness README: link 'Running a ticket' to the SDK runtime)

Verdict: **CONFIRM**. Verifier: fresh instance, archetype 8.

## Diff under test
- Landed commit `5896513a` on origin/main (parent `fc784422`), 1 file, `harness/README.md` +6, docs only.
- New paragraph at `harness/README.md:84-88`, last paragraph of `## Running a ticket`
  (heading at :59, next heading `## Fire-mode` at :90).

## Attempts to break it
1. **Anchor resolves?** Target heading `harness/README.md:525`
   `## SDK runtime: spending the plan's monthly API credit (wq#5211)`. GitHub slug rules
   (lowercase, strip punctuation except `-`/space, space→`-`) give
   `sdk-runtime-spending-the-plans-monthly-api-credit-wq5211`, which is the link's anchor. It is the
   only in-file `](#...)` link in the README, so there was no other house convention to break.
2. **Placement:** the probe finds the H1/H2 section that owns the link line: `Running a ticket`. OK.
3. **Factual claims vs code/section:**
   - "opus/sonnet/haiku": `harness/api_credit.py:73` `eligible_model_re` =
     `^(anthropic/)?claude-(opus|sonnet|haiku)|^(opus|sonnet|haiku)`. Matches.
   - `harness/fworker_sdk.py` exists. Matches.
   - "falls back to the claude CLI otherwise / once exhausted": README:568-571 (exhausted → release
     uncharged, next dispatch runs on the CLI) and :572-577 (`choose_runtime` order, explicit sdk with
     no credit → CLI). Matches.
   - "selection rules, the ledger and the off switch" are all in the target section (:558-586). Matches.
4. **Scope:** no code changes. `git show --stat 5896513a` shows only README.md. Ticket constraint
   (do not touch conftest/api_credit/harness code) honoured.
5. **Trailer:** the commit has `Co-authored-by: Lumen Industries`, but 29 of the last 30 main commits
   carry it, so lumen-pr adds it and the author did not. This is not a defect of this diff.

## Probe (re-runnable, rev-pinned): `reports/wq5221_verify_wq5220_anchor_probe.py`
Reads `harness/README.md` with `git show <rev>:`, so the input is immutable.
- `python3 reports/wq5221_verify_wq5220_anchor_probe.py 5896513a` → `unresolved: []`, owner
  `Running a ticket`, `PASS`, rc=0.
- Can fail: same rev plus a wrong-slug anchor `sdk-runtime-spending-the-plan-s-...` → `FAIL`, rc=1;
  parent rev `fc784422` (no link) → rc=1.

## Nits (non-blocking)
- "otherwise" also covers the other CLI cases (config disabled, `[no-sdk]`, fire-response,
  Fable-routed). The link points readers to the full rules, so this is acceptable.
