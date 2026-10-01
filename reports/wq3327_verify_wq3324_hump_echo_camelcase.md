# wq#3327 verify: wq#3324 [rotation] burned credential 2 values set:4b2711947fe4 (kv-secret)

Verdict: **CONFIRM**

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1536, already merged to
`main` (HEAD at the time of this verify-pass: `3791006a`, "fix(masking):
suppress camelCase label echo in kv-secret", `Refs: wq#3324`). Diff: two files,
`harness/masking.py` (+21/-3) and `harness/tests/test_masking.py` (+30), no
other files touched.

## What wq#3324/PR#1536 claimed

wq#3317's attempt 1 fired the `kv-secret` tripwire on two values
(sha256 `61b437a64a94`/`3f467a2866a0`). `mask_replay.py 3317` could not account
for them because the source (a grep of an OAuth token handler) is outside
every tracked tree the replay knows. Hashing candidates named both as the
camelCase locals `newAccessToken`/`newRefreshToken` assigned to an
`access_token:`/`refresh_token:` object-literal key — an echo of the label's
own noun, not a credential. Nothing was rotated. The fix adds
`_KV_NOT_HUMP_ECHO` to `harness/masking.py`: it suppresses a `kv-secret` match
only when the value is lowercase-led, letters-only, at most 3 camelCase humps
before the last, and that last hump is exactly the label's own noun
(case-insensitive backreference to the newly-named `nn`/`nb` groups on
`_KV_NOUN`/`_KV_BARE`), capitalised, with nothing glued after it. Recorded as
suppression `hump-echo`, auditable via `suppressions()` like every other kv
carve-out (wq#2677's contract). The env-label rule (`_KV_SECRET_ENV`) is
untouched — confirmed by reading `harness/masking.py:380-381`, which still
only carries `_KV_POINTER + _KV_NOT_CALLED + _KV_NOT_SELF`.

## What I checked — tried to break it

Read `harness/masking.py:140-420` cold and hand-traced `_KV_NOT_HUMP_ECHO`
against every edge case the PR claims still fires, independent of the
authors' own test list, before looking at their tests:

- **Digit anywhere in the value** (`newAccessToken2`) — the lowercase-run and
  hump sub-patterns are letters-only (`[a-z]`/`[A-Z][a-z]`), so a digit can
  never be absorbed; it always lands immediately after the last matched
  letter, where the trailing `(?![A-Za-z0-9._\-+/=:!#$%^*@~(])` necessarily
  fails. Guard can't fire → value is read as a credential. Matches claim.
- **ALL_CAPS noun** (`newAccessTOKEN`) — the final-hump lookahead requires
  `[A-Z][a-z]{1,19}`; an all-caps run has no lowercase second character, so
  the lookahead fails at every backtrack position. Fires. Matches claim.
- **Wrong noun** (`newAccessSecret` behind an `access_token:` label) — the
  consuming step is a literal backreference to `nn`/`nb` captured from the
  label itself; a different word can't match it. Fires. Matches claim.
- **4th hump** (`newVeryLongAccessRefreshToken`) — humps are capped `{0,3}`;
  with more humps present, every backtrack position for "this is the final
  hump" lands with more text after it, so the final-hump lookahead's own
  tail-boundary check always finds a following capital letter (a word char)
  and fails. Fires. Matches claim.
- **Uppercase-led value** (`NewAccessToken`) — the leading run is
  `[a-z]{1,20}`, minimum 1; a leading capital can't satisfy it. Fires. Matches
  claim.
- **Leading run >20 chars** — capped `{1,20}`; a longer all-lowercase lead
  before the first hump leaves a stray lowercase char the hump pattern (which
  requires an immediate `[A-Z]`) can't consume. Fires. Matches claim.
- **Duplicate named groups** — `nn` (on `_KV_NOUN`) and `nb` (on `_KV_BARE`)
  each appear exactly once in the text of every regex they're compiled into
  (`_KV_LABEL`/`_KV_LABEL_ANY` pick one or the other via alternation, never
  both); Python's `re` only forbids a name appearing twice in one pattern
  string, so this doesn't raise and isn't a latent `re.error` waiting on a
  path the tests don't hit.
- **False-suppression risk for a real secret** — the guard's shape (all
  lowercase/uppercase letters only, no digits, exact noun echo, end-anchored)
  is about as narrow as the `wq#3127`/`wq#3133` snake_case echo guard it's
  modelled on; a real random token coincidentally spelling out the label's
  own noun as its last camelCase hump with no digit anywhere is not a shape
  the fleet corpus (or any token-generation scheme) produces. Consistent with
  the existing accepted risk profile for this class of carve-out, not a new
  one.

All of these match the PR's own "still fires" list, which I then read and
found identical to my independently-derived set (`harness/masking.py:1910-1917`
in `test_masking.py`).

## Ran the probe, not just read it

```
$ python3 /home/lumen/worktrees/wq3327-a1/harness/mask_replay.py 3317
wq#3317: 1 credential-shaped values in the run's model context, 0 unaccounted
  [tracked] kv-secret ... CF_ACCESS_SERVICE_TOKEN_SECRET ...
wq#3317: 2 candidate(s) the kv-secret rule suppressed ... audit only
wq#3317: 2 hit(s) the run's tripwire recorded that today's table does not
reproduce ...: sha256=61b437a64a94, sha256=3f467a2866a0
$ echo $?
0
```

Exit 0, 0 unaccounted, and the exact two burned-value hashes from the ticket
title/notes show up as no-longer-reproduced hits — byte-for-byte what the PR
and `ISSUE-LOG.md` row 1139 claim.

```
$ python3 -m pytest harness/tests/test_masking.py -q
184 passed in 33.55s
```

Matches the PR's "184" claim exactly, including the 5 new
`hump-echo`-suppressed fixtures and the 7 new "still fires" fixtures
(`harness/tests/test_masking.py:1898-1920`).

Ran the other masking/replay/burn-filter files to check the PR's secondary
"1474 passed, 25 skipped" claim: `test_mask_foreign_prefix_matrix.py`,
`test_mask_at_rest_single_flight.py`, `test_mask_replay.py`,
`test_burn_filter.py`, `test_mask_underfloor_invariant.py`,
`test_mask_triage.py`, `test_mask_neighbour_residue.py`,
`test_mask_mirror_coverage.py`, `test_mask_at_rest.py` → **1272 passed, 25
skipped**. Adding the four other mask/replay-adjacent files I could find
elsewhere in the tree (`ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py`,
`ops/wq2218-masked-unit-provenance/test_masked_provenance.py`,
`dispatcher/tests/test_gates_replay.py`, `dispatcher/tests/test_capability_replay.py`)
→ **158 passed, 1 xfailed**, still only 13 files total against the PR's "12
other" (i.e. 13 counting `test_masking.py`), and 1430 passed vs. the claimed
1474 — a ~3% gap I could not close by file-search within this pass. This is a
loose, secondary breadth claim (not the probe, not the regex correctness, not
`ISSUE-LOG.md`'s own citation) and every file I *did* run passed outright with
no new failures — I'm not treating a rounding/selection mismatch in an aside
as a contradiction of the shipped fix, but it's noted here rather than quietly
dropped.

## Things I looked for and did not find a contradiction in

- Whether the hump-echo guard could widen the suppression surface onto a
  *real* credential shape — checked structurally (narrow, all-letter,
  digit-free, exact-noun, end-anchored) and found it no more permissive in
  kind than the already-shipped snake_case echo guard it extends.
- Whether `_KV_NOT_HUMP_ECHO` is wired into the audit trail
  (`_KV_SUPPRESSIONS`) so an overbroad match would surface as a row instead of
  going silent — yes, `harness/masking.py:415`.
- Whether the env-identifier rule (`_KV_SECRET_ENV`) picked up the new guard
  it shouldn't need — no, unchanged per the PR's own claim and my read of
  `harness/masking.py:380-381`.
- Whether this ticket needed a `--verify` handler the way `ops/` bundles do
  (per `ops/VERIFY_CONTRACT.md`) — no, this is a pure `harness/` code+test
  change with no `ops/` bundle, unlike wq#3319/wq#3325.

No incorrect or unverifiable *correctness* claim found in the diff. One
secondary test-count aside (1430 vs. claimed 1474 across "other" files) I
could not fully reproduce by file-search; does not change the verdict.

## Action taken

Confirmed on wq#3327 via `wq done`. No re-implementation of wq#3324's content;
this is a read-and-run verify-pass only.
