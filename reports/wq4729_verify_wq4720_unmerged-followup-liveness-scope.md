# wq#4729 — verify-pass on wq#4720 (PR#280): `done --unmerged` files a landing follow-up

Archetype 8 adversarial verify. Author: claude-fable-5. Verifier: claude-sonnet-5@high
(fresh instance). Verdict: **CONFIRMED** — no contradiction, no escalation.

## Access note
The gitea MCP tools (`mcp__gitea__pull_request_read` etc.) were ungranted and blocked
this verify-pass on its prior attempt. Worked around by cloning
`http://100.121.17.5:3000/lumen/lumen-bin.git` directly with `git` into
`/tmp/fworker-wq4729/lumen-bin` and fetching `refs/pull/280/head`. All evidence below is
from that clone at commit `2cfb1dc` (merge-base `8714b7d` on `main`).

## What wq#4720 claimed / asked for
`wq done --unmerged` was a deliberate, on-the-record override of the wq#899 merge-state
gate, but the `[UNMERGED-PR]` stamp it wrote had no consumer — nothing ever asked
whether the PR later landed (wq#899's `pr_recon.py` was one-shot). wq#4720 asked for
one of: (a) auto-file a landing follow-up ticket at the gate, mirroring the
`[ROOT-APPLY-FILED]` pattern (wq:3236-3243), or (b) give `pr_recon.py` a timer. PR#280
implements (a): `_file_or_reuse_unmerged_followups` (new, `wq:2984-3002`) files/reuses a
`[ops] land or close unmerged PR <slug>#<num> ...` ticket whenever `--unmerged` is given
and the forge verdict is `open` or `unknown`, stamping `[UNMERGED-FOLLOWUP: wq#N]` on the
closing ticket (`wq:3320-3330`).

## What I verified independently
1. **Diff is exactly as described:** `git diff 8714b7d 2cfb1dc -- wq
   tests/test_wq_merge_state_gate.py` — 108 lines, two files, matches the PR's own
   description (new helper + 15-line call site + 2 new/extended tests).
2. **Acceptance probe (wq#4720's own `Liveness:` line), re-run cold:**
   `grep -q UNMERGED-PR foreman/ops/*/*.py foreman/ops/*/*.sh` → true (rc 0, from the
   pre-existing wq#899 pr_recon bundle) **and independently**
   `grep -qE "unmerged.*(wq_add|file_followup|_followups)" wq` → true (rc 0, matches the
   new call site). Probe passes on either leg.
3. **Test suite, re-run on the PR branch:** `pytest tests/test_wq_merge_state_gate.py -v`
   → 3 passed, including the new `test_unmerged_close_files_a_landing_followup` (offline,
   unreachable-forge fixture, no token needed) and the existing
   `test_done_against_a_known_open_pr_accepts_with_unmerged_flag` (needs a live github
   token on this box — present, ran for real, not skipped). All 3 green.
4. **Logic walk of the new gate branch (`wq:3304-3330`):** the only way to reach
   `if unmerged:` with `verdict == "open"` is already past the `if verdict == "open" and
   not unmerged: refuse` line above it, so the refusal and the follow-up filing can never
   race each other. `verdict in ("open","unknown")` correctly excludes `landed` and
   `merged` from filing a follow-up — matches the PR's own comment ("skipped when the PR
   in fact landed: squash/cherry-pick"). Confirmed against `pr_merge_state`'s documented
   verdict set (`wq:2541-2547`): `merged`, `landed`, `open`, `unknown` — no fifth case is
   missed.
5. **Argument shape to the real liveness primitive:** the filed ticket's detail embeds
   `Liveness: pr_merged(<slug>, <num>, closed, <forge>)`. Checked against the actual
   `pr_merged(repo, n, want="merged", forge="gitea")` in
   `/home/lumen/foreman/harness/liveness.py:299` — positional order matches, `want="closed"`
   is a valid value (satisfied on merged OR closed), `forge` is derived correctly
   (`host.endswith("github.com")` → `"github"`, else `"gitea"` — the only two forges
   `pr_merged` supports, and the only two `PR_URL_RE` matches). Test output confirms this
   for both forges: `pr_merged(frankstupak/lumen-plays-pokemon, 92, closed, github)` and
   `pr_merged(lumen/some-repo, 77, closed, gitea)`.

## Things I tried to break
- **Dedup reuses a `rejected` ticket, not just an `open` one.** The dedup query in
  `_file_or_reuse_unmerged_followups` (`wq:2996-2999`) is `SELECT id, title FROM tasks
  WHERE status!='done'` — it does not exclude `status='rejected'`. If a filed follow-up
  is later rejected (Frank decides not to chase that PR) and a *different* `done
  --unmerged` close later cites the same PR, the code would silently return the rejected
  ticket's id instead of filing a fresh one, understating the debt. **Not a regression
  from this diff**: it is the exact same query, verbatim, already used by
  `_file_or_reuse_root_apply_followup` (`wq:2967`, wq#3487) and by `cmd_add`'s own
  title-dedup (`wq:1705`) — this is the established repo-wide idiom for "collapse
  repeated closes onto one ticket," not something wq#4720 introduced. Worth a follow-up
  ticket against the shared idiom, not a mark against this PR.
- **"Machine-closeable" is a bigger claim than the deployed automation delivers.** The
  new test's comment says the filed ticket is "machine-closeable the moment the PR
  merges or is closed (harness/liveness.py)". Traced the actual consumer:
  `liveness_sweep.py`'s docstring and `tickets_in_scope()` (`liveness_sweep.py:88-91`)
  default to `scope="frank"`; `liveness_clear.py:275` confirms `scope='frank' (default,
  the timer)`; the one deployed systemd unit,
  `harness/systemd/lumen-liveness-clear.service`, runs `liveness_clear.py --apply
  --write-bindings` with **no `--scope` flag** — so the only running auto-close path
  only ever looks at Frank-gated tickets (`HUMAN_LANES` or `frank_gate_reason()` —
  title markers, decision gates, or a non-empty `gate` column). The follow-up ticket
  this PR files is an ordinary build/ops-lane ticket with `gate=''` and a `[ops]` title
  (not a Frank marker) — `frank_gate_reason()` returns `None` for it, so the deployed
  timer will never evaluate its `Liveness:` line. It will sit open until a human (or a
  manual `--scope all` sweep, or a future verify-pass) looks at it — exactly the same
  fate as the `[ROOT-APPLY-FILED]` tickets it mirrors, which have had this same property
  since wq#3487. **Also inherited, not introduced.** It does not make the fix wrong:
  wq#4720's actual ask was "the override records a debt WITH an owner" (a tracked
  ticket in the open backlog, which this delivers and the probe checks), not "the ticket
  auto-closes" — that phrase is the PR's own test comment overclaiming, not an wq#4720
  acceptance criterion.
- **Race on concurrent `--unmerged` closes over the same PR:** the dedup
  select-then-insert isn't wrapped in `BEGIN IMMEDIATE` (unlike `_finish`), so two workers
  closing over the same still-open PR at the same instant could both miss each other's
  row and file two follow-ups. Same shape as wq#3174, but again the identical pattern
  already exists in `_file_or_reuse_root_apply_followup` — narrow window, inherited, not
  new.

## Verdict
CONFIRMED. The diff does what wq#4720 asked, passes its own acceptance probe run cold,
and its full test suite (3/3, including a live-github-token test that actually ran)
passes on the PR branch. The two things that looked like holes on first read — dedup
reusing a rejected ticket, and the liveness line being inert under the deployed
scope=frank timer — both turn out to be exact, pre-existing idioms this PR deliberately
mirrors (per wq#4720's own instruction to mirror `[ROOT-APPLY-FILED]`), not defects this
diff introduced. No escalation to opus-5@high.
