# wq#1795 verify-pass: wq#1754 — burn_ceiling severity/resets_at fidelity gap RCA

Diff verified: PR#495 (merged), commit 5658a85 / 753fbbe on `claude-foreman` `main`,
`reports/1754_burn_ceiling_fidelity_gap_rca.md` (86 lines, doc-only — `git show --stat`
confirms zero code files touched). Read cold, then independently re-derived every
factual claim from source and from `dispatcher.log` rather than trusting the report's
own line citations.

## What checked out

- **Code claim, `governor_pressure`** (`orchestrator/router.py:759-815`): confirmed by
  direct read — the loop at :796 only short-circuits on `b.get("percent") is None`
  (:798), reads `severity` unconditionally at :812, and never touches `resets_at`
  anywhere in the function. Matches the report's characterization exactly.
- **Code claim, `pool_spend_rate`** (`dispatcher/burn_rate.py:101-119`): confirmed —
  requires both `percent` and `resets_at` non-None (:107), requires `resets_at` to
  parse (:109-111), and requires `0 < remaining_s <= WINDOW_S[pool]` (:113-114). Stricter
  than `governor_pressure` exactly as claimed.
- **429-reuse-cache behavior**: confirmed in `governor/foreman_usaged.py:poll_once`
  (:228 onward) — on a failed poll it logs the 429 (:239) and falls through to
  `limits = (prev_state or {}).get("limits")` (found at :255, report says :250-251 —
  off by a few lines, see below), i.e. verbatim reuse of the last snapshot.
  `RELIABILITY_SPEC.md:24` (§B) matches the quoted "429s aggressively... may not
  recover for hours" language.
- **Log probe, re-run independently**: grepped `dispatcher.log` myself rather than
  trusting the report's numbers.
  - Line 72918 (`fdisp-20260911T023501Z-2060090`, 02:35:37Z): last null
    `pass_end.burn_ceiling` — confirmed byte-for-byte.
  - Lines 79565-79567 (`fdisp-20260911T040606Z-3989776`, 04:06:30-31Z): first live
    non-null `burn_ceiling`, with the exact `burn_rate`/`halt` event text quoted in the
    report — confirmed byte-for-byte, including the `no_slots` gating halt.
  - Window 04:06:30Z-06:19:24Z (log lines 79567-82993): counted `pass_end.burn_ceiling`
    entries myself — **29/29 non-null**, matches the report's count exactly.
  - Re-ran the same grep against the *current*, further-grown log (up to 06:19:46Z as
    of this verify pass): **145/145** non-null since the transition, zero nulls — the
    gap has stayed closed, not just for the report's original sample window.
  - Cross-checked the mechanism, not just the outcome: during the original tight
    window (e.g. dispatcher.log:67899, 23:32:33Z) `api_severity` reports
    `fable_weekly_scoped=critical,weekly_all=warning` — which is only reachable if
    `percent` was non-None for those pools (`governor_pressure` returns/short-circuits
    before the severity read otherwise) — while the same-timestamp `tight_mode` event
    says "no usable percent+resets_at in session,weekly_all,fable_weekly_scoped" for
    burn_rate's purposes. That's independent, non-report-derived evidence that the
    split is specifically a `resets_at` staleness failure, not a missing-`percent`
    failure — strengthening the RCA's mechanism claim beyond what it argued from
    inspection alone.

## What didn't check out (minor, non-substantive)

- The report attributes the "429s aggressively... may not recover for hours" doc
  comment to `foreman_usaged.py:fetch_primary` (:102-121). That function has no such
  comment — the quoted text actually lives in `poll_once`'s failure branch
  (:236-237), a different function ~130 lines away. Same file, correct quote, wrong
  function/line attribution. Does not affect the argument (the behavior it's citing —
  429-triggers-cache-reuse — is real and is in `poll_once`, just mis-cited).
- The `prev_state["limits"]` reuse line is :255 in the current file, not :250-251 as
  stated; off by a few lines, same function, not a substantive error.

## Adversarial checks that didn't break it

- Tried to find a case where `percent` present + `resets_at` absent could itself be
  a bug (e.g. a pool that structurally never gets `resets_at` from the API) rather
  than a transient staleness artifact — no evidence for this: the gap only appears in
  the one 429-storm window, and 145/145 passes since are clean, which is consistent
  with "transient cache staleness" and not with a structural field-mapping bug.
- Checked whether "no code change" is well-supported rather than just asserted:
  `git show --stat` on both wq#1754 commits confirms the PR is a single new report
  file, no code diff — so the "no bug, working as designed" conclusion is backed by
  the fact that nothing needed to change to get from broken-looking to healthy, other
  than time (a fresh poll landing).

## Verdict: CONFIRM

Root-cause diagnosis, code claims, and live-log evidence all independently
reproduce. The only issues found are two citation line/function slips in the RCA
prose that don't affect its correctness. No re-implementation performed per
verify-pass scope.
