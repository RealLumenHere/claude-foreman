# wq#2926 — verify-pass of wq#2899 (claude-foreman PR#1234, bd2ec41/d9587bd/4009120): CONTRADICT

Verifier: claude-sonnet-5@high, fresh instance. Date 2026-09-28. All probe values are
runtime-assembled filler or synthetic random-looking segments (`"Q"*n`, `"9"*n`,
literal prefixes built by string concatenation, e.g. `"cf"+"ut_"`), never a real or
whole credential-shaped literal typed into source or a shell command. No secret was
printed or burned in the course of this verification; probes live under
`/tmp/fworker-wq2926/` (outside any tracked repo).

## Claim under test

wq#2899 (`harness/masking.py`, `token_engine_daemon.py`, `bin/mask-secrets`): an sk-
token whose body embeds a live foreign prefix (`cfut_`/`cfut`, `xoxb-`, etc.) has the
embedded foreign token masked correctly, but sk-key's own `{16,}` floor is never
reachable once the foreign rule has already run and left `<its own kept prefix>MASKED`
behind — 3-17 characters of the sk- head printed in the clear. Fix: `_floored_absorb`/
`_floored_body` let a truncated run absorb a directly-following `<OTHER rule's bare
prefix>MASKED` into one match, mirrored in the daemon and as sed absorb lines in
`bin/mask-secrets`. Claimed 795/795 (up from 639) across masking.py + daemon + sed via
`harness/tests/test_mask_foreign_prefix_matrix.py`. A narrower exclusion is disclosed
up front and out of scope: a letter-lead fill immediately in front of a *second, literal
`sk-`* gets absorbed into sk-key's own kept prefix instead of being masked — filed as
wq#2924, excluded from the matrix via `_SK_LETTER_EXCLUDES_OWN_PREFIX`.

## What holds

Reran the shipped test suite unmodified, from this worktree's `main` (already merged):

- `harness/tests/test_mask_foreign_prefix_matrix.py`: **795 passed**, matching the
  ticket's claim.
- `harness/tests/test_masking.py`: **129 passed** (full masking suite, clean baseline).

The PR's own target shape — a live sk- body of ordinary alnum/digit filler with an
embedded foreign prefix at floor-relative offsets, where the live filler is NOT itself
letters-then-dash — is genuinely fixed: e.g. `sk-` + 6 live alnum + `-` + `cfut_` + 16
live alnum masks cleanly to `sk-MASKED` in all three implementations (verified
independently, `/tmp/fworker-wq2926/probe2.py`).

## Evidence the fix fails OPEN (contradiction)

sk-key's own prefix is `sk-(?:[a-zA-Z]+-)*` (wq#2300): it greedily repeats *any*
letters-then-dash group as part of the KEPT, unmasked capture (`sk-ant-`, `sk-or-v1-`,
...). This repeat group is not restricted to the fixed provider-tag segments the
comments cite as its motivation — any live segment shaped like `[a-zA-Z]+-` gets
absorbed into group 1, in the clear, by design. `_floored_absorb`'s new match, however,
is built as `body_run{0,} + foreign_bare_prefix + MASKED` — and when the ENTIRE live
segment between `sk-` and a following already-masked foreign residue is itself
letters-then-dash, the greedy prefix regex consumes it FIRST, leaving `body_run{0,}` to
match zero characters. The resulting overall match's post-prefix suffix is then *exactly*
`<foreign bare prefix>MASKED` with nothing else in it — precisely the shape
`_matches()`'s pre-existing `_OWN_TAIL = (?:PRE_GH|PRE_GHPAT|PRE_CFUT|PRE_SK|PRE_SLACK|
PRE_AWS)*MASKED` (wq#2638/wq#2298, "is this match just our own stale output being read
back?") fullmatches — so `_matches()` silently **drops the whole sk-key match**, even
though the `MASKED` text was produced by an EARLIER-ordered rule THIS SAME PASS, not
stale output from a prior call. `_floored_absorb`'s own docstring says the risk it
guards against is exactly this rule's OWN prior output (`sk-ant-xxxxxxxxMASKED`); it
does not account for `_OWN_TAIL` discarding a fresh, correct absorb match wholesale.

Minimal repro (`/tmp/fworker-wq2926/probe4.py`, `probe5.py`), letters-only tag segment
(`ant-`, a real Anthropic-style key convention already cited in this file's own
comments) immediately followed by a foreign residue:

| input | masking.py (this worktree) | daemon | sed mirror |
|---|---|---|---|
| `sk-ant-` + `cfut_` + 20×`9` | `sk-ant-cfut_MASKED`, `hits=['cloudflare']` — **sk-key never fires, "ant-" leaks, no sk-key record** | `sk-ant-MASKED` | `sk-ant-MASKED` |
| `sk-ant-` + `xoxb-` + 20×`Q` | `sk-ant-xoxb-MASKED`, `hits=['sk-key']` — **both "ant-" and the literal slack prefix "xoxb-" absorbed into sk-key's kept prefix, no slack record, no separate alarm on the embedded slack-shaped text** | `sk-ant-MASKED` | `sk-ant-MASKED` |
| `sk-live-` + `cfut_` + 20×`8` | `sk-live-cfut_MASKED`, `hits=['cloudflare']` (same failure, common `sk-live-`/`sk-test-` env-tag shape) | `sk-live-MASKED` | `sk-live-MASKED` |

masking.py is the one that's broken; the daemon (no `_OWN_TAIL`-equivalent screen at
all — `grep` finds none in `token_engine_daemon.py`) and the sed mirror both mask these
correctly. This is a direct hit on the PR's own stated goal ("mirrored in
token_engine_daemon.py... so the three cannot drift on this class again") — they do
drift, with masking.py under-masking relative to the other two.

Confirmed this is a real gap in the matrix, not a duplicate of the disclosed wq#2924
exclusion: `_SK_LETTER_EXCLUDES_OWN_PREFIX = {_SK}` only excludes the case where the
*foreign* substring is a second literal `sk-`; it does not exclude `cfut_`/`xoxb-`/other
foreign prefixes following a letters-dash live segment. The matrix's own fill choice
(`RULES`: `"9"` for sk-key's main run; a separate `"z"` letter-fill run that is *only*
exercised against `_SK` as the foreign substring, never combined with a literal `-`)
structurally cannot produce a letters-then-dash live segment ahead of a `cfut_`/`xoxb-`/
`AKIA`/`ghp_`/`github_pat_` embed, so this combination was never generated by
`_cases()`. `harness/tests/test_masking.py`'s own `sk-ant-` fixtures (lines 35, 100,
347, 620) are all standalone sk- tokens with a pure-`x` body — none combine `sk-ant-`
with an adjacent different-rule embed either. Grepped both test files for `ant-` /
`_OWN_TAIL` / `floored_absorb` to confirm (`/home/lumen/foreman/harness/tests/`).

This is sk-key-specific: github/github_pat/cloudflare/slack all have fixed-length
literal prefixes (no repeat group), so their own `_floored_absorb` only degenerates to
this `_OWN_TAIL`-drop at their own offset-0 boundary, where there is no live body
content to lose in the first place (independently reproduced ok, `probe.py`'s
`off0-cfut`/`off0-xox` cases) — consistent with the matrix passing there. sk-key is the
only rule whose prefix grammar can extend over live secret bytes ahead of the absorb
point.

## Severity

Realistic: `sk-ant-`, `sk-live-`, `sk-test-`, `sk-prod-`, `sk-dev-`-style pure-letter
provider/environment tags are exactly the shape `_PRE_SK`'s repeat group was added
(wq#2300) to keep visible. Any such tag immediately followed by text that happens to
open with a live foreign prefix (`cfut_`, `xoxb-`, `AKIA`, `ghp_`, `github_pat_`) —
plausible in glued log lines, JSON with no separators, or two credentials pasted
back-to-back — causes masking.py to (a) drop the sk-key alarm and hit record entirely
and (b) leak everything from `sk-` up to the foreign residue in the clear, with only the
unrelated foreign rule's own (correct) hit surviving to mislead a reader of the alarm
about what was actually present. The daemon and sed mirror do not share this defect, so
the "cannot drift" goal this PR states for itself does not hold.

## Disposition

CONTRADICT. wq#2899's disclosed target case (ordinary, non-letters-dash live filler
ahead of an absorbed foreign residue) is fixed and the 795/795 matrix claim is
accurate for what it tests. The fix introduces (or, more precisely, fails to close) an
interaction between the new `_floored_absorb` and the pre-existing `_OWN_TAIL`
own-output screen in `_matches()`, specific to sk-key's repeating letters-dash prefix
grammar, that silently drops the sk-key match and leaks live prefix segments with zero
alarm in `harness/masking.py` only — while `token_engine_daemon.py` and
`ops/wq2316-mask-secrets-boundary/mask-secrets` both handle the same input correctly,
so the three implementations drift on this input despite the PR's stated intent to keep
them in lockstep. Not re-implemented (per this ticket's instructions). ISSUE-LOG row
appended. Escalated as a `[judgment]` ticket (`claude-opus-5@high`, archetype
`rca-investigation`) per this ticket's contradiction protocol — see wq#2944.
