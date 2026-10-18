# wq#4312 — verify pass on wq#4295 (RuleBridge + Rule Coherence Graph eval), PR 2212

**Verdict: CONFIRM.** The PR's DO-NOT-ADOPT recommendation and its `research/rule_lint.py`
deliverable hold up under adversarial re-run. One factual defect found in the writeup (rule
count) — logged, does not change the recommendation, not escalated.

PR 2212 (`research/WQ4295_RULEBRIDGE_RCG_EVAL.md` + `research/rule_lint.py`, commit `b8b15bcb`)
is already merged to `main` in `/home/lumen/foreman` — verified via `git branch -a --contains
b8b15bcb` (lists `main` and `remotes/origin/main`). Gitea API/web lookups for PR 2212 both
404'd (likely auth-gated for an unauthenticated curl; not pursued further since the merged
diff was already readable locally).

## What I ran

```
python3 research/rule_lint.py --selfcheck
python3 research/rule_lint.py /home/lumen/instincts.yaml
```

- `--selfcheck`: clean fixture → 0 findings (PASS), dirty fixture → 3 findings, all three of
  the planted kinds (dup-id, dangling-ref, polarity) correctly triggered (PASS). Matches the
  script's own doctest-shaped claim exactly.
- Real corpus: **44 findings total — 0 dup-id, 2 dangling-ref, 42 polarity** (`grep -c` on
  each prefix). This is an **exact match** to the PR's claimed "0 dup-id, 2 dangling-ref, 42
  polarity findings out of 35 rules" (`research/WQ4295_RULEBRIDGE_RCG_EVAL.md:70`) — except
  for the rule count, see below.
- Exit codes: findings path → `1`, missing-file path → `2`. Matches the docstring
  (`research/rule_lint.py:30`) exactly.
- Spot-checked one polarity hit (`use-fleet-tools-first` vs `prompt-names-model`, shared
  keyword `ticket`): both rules do contain "ticket" and do carry opposite modal markers ("do
  NOT" vs "MUST"), and are substantively unrelated — confirms the PR's own characterization
  that the polarity pass is noisy/spurious, not a cherry-picked example.
- The two dangling-ref hits (`thor-only-hard-rule`→reboot-gate, `fabricated-evidence`→
  self-generated-evidence) are real supersession pointers at ids absent from the live corpus.
  Checked the file's footer directly: both are listed there as deliberately retired at the
  schema-2 restructure (wq#959/#960/#961), confirming the PR's claim that these are documented
  history, not drift.
- Independently re-searched RuleBridge via `fleet-search web` (conditions had recovered enough
  to get a DeepSeek-backed hit on npmjs.com): description, v0.1.0, and the package-manager
  drift example match the PR's characterization verbatim.

## Defect found: rule count is wrong, not drift

The PR states **35 rules** four times (verdict paragraph, §3 analysis, §4 recommendation, and
the `rule_lint.py` docstring itself: `"instincts.yaml, 477 lines / 35 rules as read
2026-10-18"`). The actual corpus has **42 rules** (8 tier-0, 30 tier-1, 4 tier-2 domain rules) —
confirmed three ways:

1. `python3 -c "... len(doc['rules'])"` against the live `/home/lumen/instincts.yaml` → 42.
2. The exact snapshot the original run captured at invocation time,
   `_runs/wq4295/snapshots/20261018T220212Z-home_lumen_instincts.yaml` → also 42.
3. File mtime (2026-09-11) predates the research commit (2026-10-18) by five weeks, so there
   is no drift window in which the file could have grown from 35 to 42 — it was already 42
   when the research ran.

So this is a miscount at research time, not a stale-fact problem. It does not change the
verdict: 42 is still a small, single-author, single-file corpus, and the actual finding counts
(0/2/42) that the recommendation is built on are exactly correct. Line count (477 vs actual
476) is within rounding/EOF noise and immaterial.

Logged to `/home/lumen/kb/ISSUE-LOG.md` row 1452. Not escalating to opus-5@high: the
reproducible, load-bearing numeric claims (finding counts, exit codes, selfcheck behavior,
RuleBridge's external description) all check out exactly; only a cosmetic corpus-size figure
is wrong, and correcting it strengthens rather than weakens the "don't adopt heavy tooling"
case (42 real rules with 42 polarity findings — the heuristic is still ~all noise).

## Recommendation

Confirm wq#4295 on the board. Suggest a follow-up docs-only fix to swap "35 rules" → "42
rules" in both files next time either is touched; not worth its own ticket.
