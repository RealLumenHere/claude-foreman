# wq#4170 — judgment: wq#4165's CONTRADICT of wq#4161 (fabricated citations)

## Verdict: UPHELD on the facts, DOWNGRADED on severity — fixed here, not filed

wq#4165 verify-passed wq#4161's GitHub Actions self-hosted-runner eval (PR#2109, merged
`0d72ee44`) and graded it CONTRADICT: the doc's conclusion is right, but two of its own
"fleet state checked" citations assert file contents that aren't there. This judgment
re-ran both greps rather than taking the verifier's word. Both reproduce, exit 1 each.

**Severity downgraded, for three reasons the verifier did not weigh:**

1. Neither citation is load-bearing. Both decorate claims already proven in place — by a
   command the author actually ran (`git config --get remote.origin.url`) plus a line-exact
   code citation, and by ISSUE-LOG #1244. Delete both and the verdict stands.
2. Finding #2 is an invented *hedge* that makes the doc's evidence look weaker than reality
   ("zero hits" beats "beyond an unrelated comment match"). Wrong in the direction that
   cannot mislead a reader into an action.
3. Finding #1's general claim is true (179 of `reports/*.md` carry the Gitea host) and the
   wrong exemplar is a filename-collision mis-pick: `reports/` holds three near-identically
   named files for this thread, and `wq3714_verify_wq3709_...md:4` really does carry that PR
   URL. Still fabrication, but over a true fact.

Worth the edit, not worth the dispatch — same disposition as wq#3709 and as wq#3978's STRIP
of wq#3941's fabricated docstring path (ISSUE-LOG #1335). **No rework on wq#4161.**

## What this PR contains

- `reports/wq4170_judgment_wq4165_wq4161_fabricated_citations.md` — the judgment, with the
  re-run greps, the severity reasoning, and a process finding: a verify-pass that upholds
  the conclusion and faults only non-load-bearing evidence is a CONFIRM-with-defects;
  grading it CONTRADICT auto-escalated a two-line doc edit into a full opus dispatch.
- `research/wq4161_github_actions_self_hosted_runner_eval.md:9-20` — invented exemplar
  swapped for `reports/wq3859_judgment_wq3849_m80s-llm-deployment.md:3`, a real Gitea PR URL
  in a file the doc already cites for M80s status, plus an inline correction note.
- `research/wq4161_github_actions_self_hosted_runner_eval.md:65-68` — invented hedge replaced
  with the exact grep and its real exit code.
- ISSUE-LOG #1405.

wq#4161's recommendation #1 (file a distinct `gitea/act_runner` evaluation) is untouched and
still the real follow-up.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
