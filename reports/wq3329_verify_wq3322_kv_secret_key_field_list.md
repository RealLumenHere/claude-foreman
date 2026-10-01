# wq#3329 verify: wq#3322 [rotation] burned credential sha256:02825b2494fc (kv-secret)

Verdict: **CONFIRM**

PR: foreman PR#1538, merged to `main` as `d2ae15e9` (already an ancestor of
`main` in this worktree). Diff: `harness/masking.py` (+10/-1),
`harness/tests/test_masking.py` (+17 new test), `token-engine/token_engine_daemon.py`
(+4/-1, the daemon's hand-maintained mirror of the same kv-secret rule).

## What wq#3322/PR#1538 claimed

wq#3314's ewf-flasher finding quoted a composite-key field list —
`deviceKey = bus:port:vid:pid.` — and the kv-secret rule's existing
`_KV_NOT_IDENT` guard (which already excuses `_`-joined snake_case identifier
values on a `...Key`-labelled line, wq#2806) didn't cover a `:`-joined one, so
it fired as a 17-char burned credential (sha256 `02825b2494fc`) on both
wq#3314 and wq#3323. The fix adds `_KV_KEY_FIELDS =
r"[a-z]{1,20}(?::[a-z]{1,20}){1,7}"` (letters-only segments, 2-8 total,
joined by `:`) as a second alternative inside `_KV_NOT_IDENT`, with the same
"body must then end, optional trailing `[.,;!?]`" discipline the existing
guard already applies — so a value that starts like a field list but keeps
going past it (a digit, more unbroken text) still fires. Applied identically
to `harness/masking.py` and the independent regex copy in
`token-engine/token_engine_daemon.py`. Claims no rotation was needed (the
value is prose, never left the tracked tree) and cites `mask_replay.py`
against wq#3314 and wq#3323 both reporting 0 unaccounted.

## What I checked

- **Read the diff cold** (`git show d2ae15e9`) — three files, matches the PR
  description exactly: the new `_KV_KEY_FIELDS` pattern, its insertion into
  `_KV_NOT_IDENT`'s alternation in both `masking.py` and the daemon mirror,
  and one new test (`test_a_key_described_by_its_fields_is_an_identifier`).
- **Ran the shipped test** — `pytest harness/tests/test_masking.py -k
  key_described_by_its_fields` passes; covers the repro value plus three
  trailing-punctuation tails, the over-long combined value, a digit-bearing
  segment, an opaque non-colon tail, a bare-word non-field-list value, and a
  `dbPassword`-labelled field-list value (guard is `key`-noun only).
- **Full regression** — `pytest harness/tests/test_masking.py` 185/185 pass;
  `pytest token-engine/tests/test_daemon.py` 61/61 pass. No test was added
  for the daemon's own copy of the fix (only `masking.py`'s copy got a new
  test) — a real coverage gap, but I independently exercised the daemon's
  `mask_rewrite`/`mask_hits` directly (see below) and it behaves identically,
  so the gap didn't hide a live bug.
- **Replayed both tickets the fix claims to settle** —
  `python3 harness/mask_replay.py 3314` and `... 3323`: both report "0
  credential-shaped values ... 0 unaccounted" and list sha256 `02825b2494fc`
  as a `[identifier]`-suppressed kv-secret candidate, exactly matching the
  PR's claim and the ISSUE-LOG #1141 entry.
- **Adversarial boundary probes** (ran directly against `harness/masking.py`
  and, via `sys.path` to `/home/lumen/bin` for `fleet_exec_policy`, against
  `token_engine_daemon.py`'s own `mask_rewrite`):
  - Repro value suppressed in both copies. ✓
  - A digit glued onto the last segment (`bus:port:vid:pid12`) still fires in
    both copies — the "body must end here" lookahead isn't fooled by a
    field-list-shaped prefix. ✓
  - A long unbroken letter run appended after the field list
    (`...pidSECRETTAILmoretext...`) still fires — not just a prefix match. ✓
  - 9 colon-joined segments (past the pattern's own `{1,7}` cap, so 9 total)
    still fires; 8 segments (`1 + 7` repeats) is the pattern's own documented
    ceiling and is correctly suppressed — not a bug, just the boundary the
    regex itself declares. ✓
  - A first segment over 20 chars still fires (segment-length cap enforced). ✓
  - Performance: a worst-case 8-segment value plus 500 trailing chars and a
    2000-token line with an embedded field list both resolve in low
    milliseconds — no catastrophic backtracking from the new alternation. ✓
- **Compile check** — `python3 -m py_compile harness/masking.py
  token-engine/token_engine_daemon.py` clean.
- **Ledger / board** — `wq show 3322`: `status: done`, notes cite PR#1538,
  the replay results, and ISSUE-LOG row 1141; no rotation was recorded (none
  needed — confirmed independently via the replay above, not just taken on
  the ticket's word).

## Residual risk noted, not a contradiction

`_KV_KEY_FIELDS`, like the pre-existing `_KV_IDENT_VALUE` snake_case guard it
sits beside, is a heuristic: any `...Key`-labelled line whose value happens
to be 2-8 lowercase-letter-only colon-joined segments is suppressed,
including hypothetical real secrets shaped that way (confirmed
`privateKey = us:east:prod:main` and `signingKey = alpha:bravo:charlie` both
suppress). This is the same accepted tradeoff class the underscore-joined
guard already carries for `...Key` labels generally, not a new risk this fix
introduces, and real key material (base64/hex/mixed-case tokens) essentially
never takes this shape. Not filing a contradiction over it.

## Action taken

Confirmed on wq#3329/wq#3322 via `wq done`. No re-implementation — verify
only.
