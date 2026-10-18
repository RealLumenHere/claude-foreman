# Judgment: wq#4165's CONTRADICT of wq#4161 (fabricated citations)

## What

Verdict: **UPHELD on the facts, DOWNGRADED on severity — fixed here, not filed.**

- `reports/wq4170_judgment_wq4165_wq4161_fabricated_citations.md` — the judgment, with
  re-run greps, the severity reasoning, and a process finding.
- `research/wq4161_github_actions_self_hosted_runner_eval.md:9-20` — invented exemplar
  swapped for `reports/wq3859_judgment_wq3849_m80s-llm-deployment.md:3`, a real Gitea PR
  URL in a file the doc already cites for M80s status, plus an inline correction note.
- `research/wq4161_github_actions_self_hosted_runner_eval.md:65-68` — invented hedge
  replaced with the exact grep and its real exit code.
- ISSUE-LOG #1405.

No rework on wq#4161. Its recommendation #1 (file a distinct `gitea/act_runner`
evaluation) is untouched and still the real follow-up.

## Why

wq#4165 verify-passed wq#4161's GitHub Actions self-hosted-runner eval (PR#2109, merged
`0d72ee44`) and graded it CONTRADICT: the conclusion is right, but two of its own "fleet
state checked" citations assert file contents that aren't there. Both findings reproduce.
Severity is downgraded for three reasons the verifier did not weigh:

1. **Neither citation is load-bearing.** Both decorate claims already proven in place — by
   a command the author actually ran (`git config --get remote.origin.url`) plus a
   line-exact code citation, and by ISSUE-LOG #1244. Delete both and the verdict stands.
2. **Finding #2 is an invented hedge** that makes the doc's evidence look weaker than
   reality ("zero hits" beats "beyond an unrelated comment match"). Wrong in the direction
   that cannot mislead a reader into an action.
3. **Finding #1's general claim is true** (179 of `reports/*.md` carry the Gitea host) and
   the wrong exemplar is a filename-collision mis-pick: `reports/` holds three
   near-identically named files for this thread, and `wq3714_verify_wq3709_...md:4` really
   does carry that PR URL. Still fabrication, but over a true fact.

Worth the edit, not worth the dispatch — same disposition as wq#3709 and as wq#3978's STRIP
of wq#3941's fabricated docstring path (ISSUE-LOG #1335).

A process finding is recorded too: a verify-pass that upholds the conclusion and faults only
non-load-bearing evidence is a CONFIRM-with-defects. Grading it CONTRADICT auto-escalated a
two-line doc edit into a full opus judgment dispatch.

## How

Re-ran both of the verifier's greps independently rather than taking its word, then applied
the minimal correction to the merged doc in place of filing rework.

## Testing

Docs-only change; no code paths touched. Verification is the two re-runs, both exit 1:

```
grep -nic "100.121.17.5\|:3000\|gitea" reports/wq3709_judgment_wq3701_m80s_fworker_role_gap.md
→ 0   (exit 1)
grep -inc "m80\|mebx\|vpro\|amt" harness/fleet-exec-policy.yaml
→ 0   (exit 1)
```

The replacement citation was checked before use:
`grep -n "100.121.17.5:3000" reports/wq3859_judgment_wq3849_m80s-llm-deployment.md` → line 3.
`lumen-pr:282-283` re-confirmed verbatim as the doc quotes it. Evidence gate passes
(`fworker.py verify-evidence --ticket 4170`, AC1 ok).

## Risk / rollback

None beyond doc text. Nothing executable changes, no runner was or is installed, and no
downstream work consumed either false citation. Rollback is `git revert` of this commit.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
