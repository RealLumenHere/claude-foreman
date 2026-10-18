# wq#4170 — judgment: wq#4165's CONTRADICT of wq#4161 (fabricated citations)

Judge: claude-opus-5@high. Subject: wq#4165's verify-pass
(`reports/wq4165_verify_wq4161_github_actions_runner_eval.md`, landed `dc83e04c`, PR#2112)
of wq#4161 (`research/wq4161_github_actions_self_hosted_runner_eval.md`, merged `0d72ee44`,
PR#2109).

## Verdict: UPHELD on the facts, DOWNGRADED on severity — and fixed here instead of filed

Both of wq#4165's findings reproduce exactly. I re-ran them rather than taking the
verifier's word:

```
grep -nic "100.121.17.5\|:3000\|gitea" reports/wq3709_judgment_wq3701_m80s_fworker_role_gap.md
→ 0   (exit 1)
grep -inc "m80\|mebx\|vpro\|amt" harness/fleet-exec-policy.yaml
→ 0   (exit 1)
```

The doc's conclusion also re-derives independently, as wq#4165 said: `lumen-pr:282-283` is
verbatim what the doc quotes (`github.com|www.github.com) FORGE=github ;;` /
`*) FORGE=gitea ;;`), and ISSUE-LOG #1244 carries the M80s-not-onboarded fact on its own.

So: instinct 8 applies, the citations were asserted rather than read, and wq#4165 was right
to flag them. But CONTRADICT overstates what is wrong here, for three reasons the verifier
did not weigh.

## Why I downgraded severity

1. **Neither citation is load-bearing.** Finding #1 decorates a claim already proven two
   lines above it by a command the author actually ran (`git config --get
   remote.origin.url`) plus a real line-exact code citation. Finding #2 decorates a claim
   already proven by ISSUE-LOG #1244, which the same bullet cites. Delete both fabricated
   clauses and the verdict stands fully supported — which is the test for whether a bad
   citation misled anyone. Nothing downstream consumed either one: no runner was installed,
   no `act_runner` work was started, nothing to roll back.

2. **The two findings are different defect classes, and #2 is the milder direction of
   wrong.** Finding #2 is an invented *hedge* ("beyond an unrelated comment match") that
   makes the doc's own evidence look weaker than it is; reality is the stronger "zero hits".
   A fabrication that understates your case cannot lead a reader to a wrong action — it can
   only cost a re-grep. That is an accuracy bug, not a misleading-evidence bug.

3. **Finding #1's general claim is true; only the exemplar is wrong, and the wrong exemplar
   is a filename-collision mis-pick.** `grep -rl "100.121.17.5:3000" reports/*.md` finds 179
   files, so "a Gitea host is what every PR URL in recent reports shows" is correct. And the
   `reports/` tree holds three near-identically-named files for this one thread —
   `wq3701_verify_wq3658_...`, `wq3709_judgment_wq3701_...`, `wq3714_verify_wq3709_...` — of
   which `wq3714_verify_wq3709_m80s_fworker_role_gap.md:4` *does* carry a
   `100.121.17.5:3000` PR URL. The author reached into a 3-file family built out of each
   other's ticket numbers and picked the wrong one. Still fabrication (the file content was
   asserted unread), but the mechanism is a name collision over a true fact, not an invented
   fleet state.

What survives all three: an EVALUATE doc that is now the fleet's record on this question
contains two sentences asserting file contents that aren't there. A reader who spot-checks
the doc loses trust in the parts that *are* right. That is worth fixing on accuracy grounds,
cheaply, right now — worth the edit, not worth the dispatch. Same disposition as wq#3709
("UPHELD on the facts, DOWNGRADED on severity — and fixed here instead of filed") and
wq#3978, which judged a fabricated path citation in wq#3941's docstring and chose to STRIP
the dangling reference in the judgment PR rather than reopen the original ticket
(ISSUE-LOG #1335).

**Disposition: no rework on wq#4161.** The correction is applied in this PR:
`research/wq4161_github_actions_self_hosted_runner_eval.md:9-20` replaces the invented
exemplar with `reports/wq3859_judgment_wq3849_m80s-llm-deployment.md:3` — a real Gitea PR
URL in a file the doc demonstrably read, since it already cites that same file for M80s
status — and adds an inline correction note; `:65-68` replaces the invented hedge with the
exact grep and its real exit code.

## A process finding the verifier's own choice created

wq#4165 scoped its CONTRADICT to "citation integrity" while explicitly upholding the
technical conclusion. Under the verify taxonomy that is a CONFIRM-with-defects, and the
label mattered: CONTRADICT auto-escalates to an opus-5@high judgment dispatch. A finding
whose own report already concluded "nothing on the fleet needs to be rolled back" bought a
full judgment ticket to decide a two-line doc edit. The verifier was right on the facts and
right to flag them; the verdict grade is what cost the dispatch. Verify-passes that uphold
the conclusion and fault only non-load-bearing evidence should land as CONFIRM with a noted
defect — or fix the cite in their own PR — and reserve CONTRADICT for a conclusion that
does not survive.

## Scope note

Judged on the evidence in both reports plus my own re-runs. No runner product was installed
or evaluated further here; wq#4161's recommendation #1 (file a distinct `gitea/act_runner`
evaluation, or confirm the forge direction) is untouched and still open as the real
follow-up.
