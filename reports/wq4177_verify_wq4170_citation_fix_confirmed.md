# wq#4177 — verify: wq#4170's judgment on wq#4165's CONTRADICT of wq#4161

Archetype 8 adversarial verify-pass. Subject: wq#4170 (claude-opus-5@high judgment,
`reports/wq4170_judgment_wq4165_wq4161_fabricated_citations.md`, PR#2116, merge commit
`6313be40`, content commit `4a566ea7`) ruling on wq#4165's verify-pass CONTRADICT
(`reports/wq4165_verify_wq4161_github_actions_runner_eval.md`, PR#2112, `dc83e04c`) of
wq#4161's EVALUATE doc (`research/wq4161_github_actions_self_hosted_runner_eval.md`,
PR#2109, `0d72ee44`). Verifier: claude-sonnet-5@high (fresh instance).

## Verdict: CONFIRM

wq#4170 read cold and attacked on every factual claim it makes. Nothing breaks. No new
fabrication was introduced while fixing the old one.

## What I re-ran independently (not trusting any prior model's word)

1. `grep -nic "100.121.17.5\|:3000\|gitea" reports/wq3709_judgment_wq3701_m80s_fworker_role_gap.md`
   → `0`, exit 1. Confirms wq#4165's finding #1 reproduces.
2. `grep -inc "m80\|mebx\|vpro\|amt" harness/fleet-exec-policy.yaml` → `0`, exit 1.
   Confirms wq#4165's finding #2 reproduces.
3. `sed -n '278,286p' /home/lumen/bin/lumen-pr` — lines 282-283 are verbatim
   `github.com|www.github.com) FORGE=github ;;` / `*) FORGE=gitea ;;` as both wq#4161 and
   wq#4170 quote.
4. `grep -n "1244" /home/lumen/kb/ISSUE-LOG.md` row 1393 — carries the exact "zero hits"
   wording wq#4165/wq#4170 both cite, and the M80s-not-onboarded fact.
5. The three near-identically-named report files wq#4170 says the author collided between
   do all exist: `wq3701_verify_wq3658_m80s_fworker_role_gap.md`,
   `wq3709_judgment_wq3701_m80s_fworker_role_gap.md`,
   `wq3714_verify_wq3709_m80s_fworker_role_gap.md`. The third really does carry a
   `100.121.17.5:3000` PR URL at line 4 (`sed -n '1,8p'` confirms) — the filename-collision
   theory for finding #1's mechanism holds.
6. **The replacement citation wq#4170 swapped in was checked for fabrication too**, since a
   fix-the-fabrication PR introducing a fresh fabrication would be the sharpest possible
   break: `reports/wq3859_judgment_wq3849_m80s-llm-deployment.md:3` does carry a
   `100.121.17.5:3000` PR URL (`http://.../pulls/1844`), and the doc already cites that same
   file elsewhere (lines 63, 65 in the corrected doc) for M80s status, so "a file the doc
   demonstrably read" holds. Clean.
7. `research/wq4161_github_actions_self_hosted_runner_eval.md` at current HEAD: the
   "Correction" blockquote (lines 14-20) correctly names the section below it
   ("Fleet state checked" is in fact the line 58 heading), and the corrected grep command at
   lines 65-68 matches what I independently ran in (2) verbatim, with the right exit code.
8. ISSUE-LOG #1405 (added by wq#4170) matches both reports' content; ISSUE-LOG #1335
   (precedent: wq#3978 STRIP on wq#3941's fabricated docstring path) and wq#3709's own
   notes ("UPHELD on the facts, DOWNGRADED to low-severity doc accuracy, and FIXED in place")
   both check out as accurately characterized precedents, not invented ones.

## One discrepancy found — not a fabrication

wq#4170's doc edit claims "179 of `reports/*.md` carry that host"
(`research/...eval.md:12`). Running `grep -rl "100.121.17.5:3000" reports/*.md | wc -l`
today returns **180**, not 179. This is not a fabricated count: the judgment report itself
(`reports/wq4170_judgment_wq4165_wq4161_fabricated_citations.md`, landed in the same commit)
quotes the host string twice in its own prose, so it joined the corpus being counted after
the 179 figure was presumably taken. The count was true at the moment it was computed and
is now stale by exactly one, for a reason that has nothing to do with the claim's substance
(real citations in real reports at a real Gitea host). Noted for the record, not worth a
correction — the underlying claim ("a Gitea host is what every PR URL in recent reports
shows") is true at either count.

## Why this isn't CONTRADICT

Every reproducible claim reproduces. The severity-downgrade reasoning (neither original
citation was load-bearing; finding #2 is a hedge in the safe direction; finding #1 is a
true general claim over a wrong exemplar picked from a 3-file name collision) is internally
consistent with the evidence and doesn't overclaim beyond what the re-run greps support.
The fix applied in-PR is minimal, accurate, and does not introduce a new unverified claim —
the one new citation it adds was itself checked here and holds.

## Scope note

I did not re-evaluate wq#4161's underlying technical recommendation (Gitea vs
`actions/runner`) — that was never in dispute by wq#4165 or wq#4170, and re-deriving it is
outside this verify-pass's scope (it was already independently confirmed twice, by wq#4165
and by wq#4170). No escalation. No judgment ticket filed.
