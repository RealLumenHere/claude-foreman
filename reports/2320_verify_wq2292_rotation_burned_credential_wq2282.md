# wq#2320 — verify of wq#2292 (claude-foreman PR#813, merged squash e0a4d06)

Verifier: claude-sonnet-5@high, fresh instance. Author: claude-opus-5. Verdict:
**CONFIRM** — no contradiction, no escalation.

wq#2292 claims the wq#2275 mask tripwire that fired on wq#2282 attempt 1's
stream-json (label `github`) was a **false positive** — `ghs_` inside the repo
name `highs_solver`, quoted verbatim in `kb/ISSUE-LOG.md` rows 548/554 as the
worked example of the identical bug already fixed in `remote_credential_sweep.py`
(wq#2263) but never carried across — and that **nothing needs rotating**. The
fix anchors every prefix rule (github, cloudflare, sk-key, slack, aws) on
`(?<![A-Za-z0-9])` in both `harness/masking.py` and its
`token-engine/token_engine_daemon.py` mirror, and raises the github body floors
to real token widths (30 for `gh?_`, 20 for `github_pat_`). I did not trust the
author's write-up at face value; I reproduced every load-bearing claim
independently against the frozen run evidence and the current repo.

## Per claim

| wq#2292 claim | Result | Evidence |
|---|---|---|
| The `github` rule live at alarm time was unanchored with a 6-char body floor | CONFIRMED | The branch tip (`3b22683`, `fix/wq2292-rotation-burned-credential-in-wq-2282-tr-a1`) and the squash-merge (`e0a4d06`) carry an identical patch — diffed both `^..commit` patches for `harness/masking.py` + `token_engine_daemon.py`; only hunk line-offsets differ (base drift), the changed lines are byte-identical. |
| All 4 github-shaped strings in the wq#2282 run dir are `highs_solver` (10 chars), not a real 36-40 char token, and no `*_MASKED` residue exists anywhere in the run | CONFIRMED | `_runs/wq2282` snapshot contents inspected directly; matches the done-notes. |
| Replay probe: OLD unanchored rule alarms on the frozen wq#2282 ISSUE-LOG snapshot, NEW anchored rule is silent, a synthetic real token is still caught | CONFIRMED, independently reproduced | Ran both regexes myself (not the author's numbers) against `_runs/wq2282/snapshots/20260920T221630Z-home_lumen_kb_ISSUE-LOG.md`: OLD → 2 matches (`ghs_`, `ghs_`); `masking.mask_text()` (current) → `[]`; synthetic `ghp_`+36 → `['github']`, masked to `ghp_MASKED`. |
| Fix scope: anchor is a word boundary only (no underscore yet), narrow to this PR — does not claim to solve underscore-adjacent false positives | CONFIRMED, and the gap is real but out of scope | `_B = r"(?<![A-Za-z0-9])"` at this commit, confirmed via the diff. An underscore-adjacent case (`notes_on_xoxb-…`) is *not* covered by this boundary — but that is a distinct, later-discovered bug, already filed and fixed as wq#2400 (`_B` on current main reads `(?<![A-Za-z0-9_])`, with its own docstring explaining exactly this gap). Not a defect in wq#2292's diff; it solved the bug it was filed for. |
| Probe/regression tests pass, no regressions | CONFIRMED, with one unrelated pre-existing red | `pytest harness/tests/test_masking.py -q`: 63 passed, 1 failed. The failure (`test_doctrine_corpus_does_not_re_arm_the_alarm`) only reports `cloudflare` and `kv-secret` label hits — zero `github` hits — and the test itself was added by a *later* commit (`64b4e50`/`28ae165`, not part of PR#813); it fails today because `kb/ISSUE-LOG.md` has grown ~5 days of further false-positive documentation since the merge, an orthogonal and already-tracked class of the same quine problem for different labels. `pytest token-engine/tests/test_daemon.py -q`: 60 passed, 0 failed. |
| kv-secret pattern deliberately left unanchored (keys on an English word, not a prefix; anchoring would miss `githubToken: ...`) | CONFIRMED | Diffed `token_engine_daemon.py`'s `kv_secret` pattern before/after the commit — untouched; `harness/masking.py`'s kv-secret regex likewise carries no `_B` prefix. Consistent with the stated rationale. |

## What I tried to break

- Checked whether the two apparently-duplicate commits (`3b22683` on the stale
  local branch vs. `e0a4d06`, the actual squash merge, 3 minutes apart) hid a
  divergence between what was reviewed and what shipped — they don't: diffing
  the two patches for the core logic files shows only line-number hunk-header
  drift from a slightly newer base, zero content difference.
- Looked for a real credential shape the raised floors (30/20) could now miss.
  GitHub's classic PAT bodies are a fixed 36-char base62 string and fine-grained
  PATs run ~82 chars with underscores — both comfortably clear the new floors;
  the synthetic-token regression test in `test_masking.py` (`GHP`, `GHPAT`)
  covers both shapes and passes.
- Tried to find a case where the body floor alone (without the boundary) would
  have been sufficient, to see if the boundary was dead weight — it isn't:
  `clone git@github.com:o/ghp_notes.git` is boundary-safe (preceded by `/`) but
  floor-blocked (`notes.git` < 30 chars); `highs_solver`'s `ghs_` is
  boundary-blocked (preceded by `i`) even though nothing bounds it on body
  length alone in adjacent short-body cases. Both defenses are load-bearing for
  different false-positive shapes in the author's own test list.
- Ran the failing `test_doctrine_corpus_does_not_re_arm_the_alarm` alone to
  confirm it carries zero `github`-labelled hits, ruling out that it's silently
  re-exposing the exact bug wq#2292 fixed under a different guise. It isn't —
  the residual hits are `cloudflare`/`kv-secret`, a different pattern class
  entirely.
- Considered escalating on the underscore gap, then verified it was already
  filed and fixed as wq#2400 with its own regression coverage — re-flagging it
  here would be redundant, not a new finding.

## Conclusion

Every load-bearing claim in wq#2292's done-notes reproduces independently: the
unanchored pre-fix rule, the false-positive shape and its absence of any masked
residue in the wq#2282 run, the replay probe's exact OLD/NEW/synthetic
outcomes, and the deliberate, correctly-scoped exclusion of kv-secret from the
anchor. The one red test in the current suite is unrelated to this PR's scope
(different labels, added by a later commit, tracking a live-corpus-growth
problem). No credential was burned. CONFIRM.
