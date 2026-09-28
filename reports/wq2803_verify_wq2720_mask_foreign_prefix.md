# wq#2803 — verify-pass of wq#2720 (claude-foreman PR#1131, 2f538e8): CONTRADICT

Verifier: claude-sonnet-5@high, fresh instance. Date 2026-09-28. All probe values are
runtime-assembled filler (`"Q" * n`), never a real or credential-shaped literal typed
into source or a shell command; no secret was printed or burned in the course of this
verification.

## Claim under test

wq#2720 / PR#1131 (merged 2f538e8, `harness/masking.py` + `ops/wq2316-mask-secrets-boundary/mask-secrets`):
"the github rule's wide body charset can swallow a glued-on foreign prefix
(`AKIA`, `xoxb-`, `cfut_`, `sk-`, `github_pat_`) whole, leaving the next rule's token
unmasked; fixed by making `_body()`'s greedy run stop at any FOREIGN rule's fixed
prefix (`_foreign_prefixes`), looped up to `len(PATTERNS)` passes so a rule freed by
one stop gets a later shot." Repro `test_the_wq2702_triple_glue_masks_its_slack_half`
(`ghp_<body>AKIA<16>xoxb-<body>`) is asserted fixed; xfail dropped.

## What holds

The PR's own target case is genuinely fixed. Reran it independently
(`/tmp/fworker-wq2803/probe_wq2803.py`, `/tmp/fworker-wq2803/probe_wq2803_v2.py`):

- `ghp_` + 40-char body + `AKIA` + 16-char body + `xoxb-` + 11-digit body →
  post-fix `hits=['github','aws','slack']`, fully masked. Pre-fix (`2f538e8^`)
  → `hits=['github']` only, slack half still live. The narrow bug is closed.
- `harness/tests/test_masking.py`: 101 passed, 1 failed. The 1 failure
  (`test_the_mask_suite_does_not_arm_its_own_tripwire`, a committed `kv-secret`
  shape in `reports/2787_verify_wq2782_mask_tripwire_cloudflare_hold.md`) is
  unrelated to wq#2720's diff — a separate corpus-hygiene issue from the
  wq#2787/wq#2839 lineage, not touched by this PR. Not investigated further here
  (out of scope for this verify-pass); flagged so it isn't mistaken for fallout of
  this fix.

## Evidence the fix fails OPEN (contradiction)

`_body(charset, quant, foreign)` stops the greedy body run at a foreign prefix via
`(?:(?!(?-i:MASKED|foreign))charset)quant`. Each repetition of that group consumes
one character; if the run is halted before `quant`'s minimum (`{30,}` github,
`{20,}` github_pat, `{6,}` cloudflare, `{16,}` sk-key, `{8,}` slack) is satisfied,
the whole rule fails to match at that position — not "matches a shorter prefix",
**fails entirely** — and since the truncated segment is exactly what would have
been the credential's own prefix + body, the ENTIRE token (fixed prefix included)
is left byte-for-byte in the clear, with zero hits and zero alarm. This is worse
than the bug wq#2720 fixed: that bug left half a *glued second token* unmasked and
still alarmed on the first; this leaves 100% of *one* token unmasked with no alarm
at all.

Reproduced independently, unconfounded (foreign prefix placed well before the
floor, but total body length kept well ABOVE the floor so the failure is
attributable to the stop condition alone, not to a merely-short body):

| rule | input shape (offsets are char counts) | pre-fix (`2f538e8^`) | post-fix (2f538e8, this worktree) |
|---|---|---|---|
| github (floor 30) | `ghp_` + Q×10 + `AKIA` + Q×30 (body=44) | `hits=['github']`, masked | `hits=[]`, **0-byte change, full token in the clear** |
| cloudflare (floor 6) | `cfut_` + Q×3 + `AKIA` + Q×16 (body=23) | `hits=['cloudflare']`, masked | `hits=[]`, **leaked** |
| sk-key (floor 16) | `sk-ant-` + Q×8 + `AKIA` + Q×16 (body=28) | `hits=['sk-key']`, masked | `hits=[]`, **leaked** |

Script: `/tmp/fworker-wq2803/probe_wq2803_v2.py` (imports `masking` from this
worktree's `harness/`, and the pre-fix module extracted via
`git show 2f538e8^:harness/masking.py`). Confirms wq#2848's finding (filed by a
prior attempt of this same verify-pass ticket) independently, from scratch, with
tighter isolation of the confound (wq#2848's own probe path was not re-run here
since it lives under `/home/lumen/scratch/repo-verify`, outside this session's
allowed dirs — its numbers were not taken on faith, this report's are a fresh
derivation).

**The same defect exists in the sed mirror shipped in the same PR**
(`ops/wq2316-mask-secrets-boundary/mask-secrets`), which uses an `\x02`
bracket-and-strip in place of a lookahead but has the identical stop-before-floor
failure mode:

| rule | input | leaked (sed mirror) |
|---|---|---|
| github | `ghp_`+Q×10+`AKIA`+Q×30 | **True** |
| cloudflare | `cfut_`+Q×3+`AKIA`+Q×16 | **True** |
| sk-key | `sk-ant-`+Q×8+`AKIA`+Q×16 | **True** |

Script: `/tmp/fworker-wq2803/probe_sed_mirror.py`, invoking the shipped
`mask-secrets` script directly via stdin.

## Severity

Natural rate is low for `AKIA` inside a github token (4 fixed uppercase-only
chars in a 62-char alphabet), but `cloudflare`/`sk-key`/`kv-secret`-adjacent bodies
whose charset includes `-`/`_` can contain `sk-`, `cfut_`, or `xoxb-` at a
materially higher rate, and the failure mode is total (whole token, zero alarm) —
strictly worse than "some chance of a partial glue-leak with an alarm still
firing," which is what shipped before wq#2720.

## Disposition

CONTRADICT. wq#2720's target case is fixed; the fix itself introduces a new,
broader, silent (no-alarm) fail-open regression across `github`, `cloudflare`,
and `sk-key` in both `harness/masking.py` and its `bin/mask-secrets` sed mirror.
Not re-implemented (per this ticket's instructions). wq#2848 (pri 1, filed by a
prior attempt of this ticket, currently claimed/in progress) already tracks the
mechanical fix ("stop only where the foreign rule's FULL shape matches, not a
bare prefix substring") — left untouched here since it is actively being worked.
Escalated as a `[judgment]` ticket (opus-5@high) per this ticket's contradiction
protocol, superseding attempt 1's incomplete escalation (it filed wq#2848 as a
same-model follow-up rather than an opus-5@high judgment ticket). ISSUE-LOG row
appended.
