# wq#2632 — judgment: wq#2288's kv-secret trailing lookahead drops matches, not narrows them

Ruling on the escalation wq#2322's verify-pass raised against wq#2288 / PR#815 / `32aa000`.
Written 2026-09-25 (opus-5, `deps=2288,2322`). wq#2288 stays done and PR#815 stays merged;
this ticket changes the rule going forward, it does not revisit that gate.

## The escalation was correct on fact

Re-derived here rather than taken on the ticket's word, against the current worktree:

| Claim | Status | Evidence |
|---|---|---|
| both copies carried `(?![A-Za-z0-9._-]\|\()` | **confirmed** | pre-fix `harness/masking.py:137` and `token-engine/token_engine_daemon.py:125` |
| the charset half is inert | **confirmed** | the body run is greedy with nothing after it; probe variant `shipped_plus_charset` returns the identical hit set over 486 files |
| an opaque value glued to `(` is missed entirely | **confirmed** | pre-fix `mask_text("TOKEN=" + 22 opaque chars + "(")` → `('TOKEN=…(', [])` — no mask, no label, no scan record |
| this is a regression from pre-wq#2288 behaviour | **confirmed** | `/tmp/fworker-wq2632/falsify.py` re-runs the new assertions against the wq#2288 rule: 3 of 3 fail with "missed entirely" |
| the daemon copy has the same hole | **confirmed** | `ted.mask_hits()` on the same three lines, now pinned in `test_token_engine_mirror_agrees` |

The mechanism the ticket named is exact and worth restating, because it is the part that
makes this worse than a narrowing. A greedy `[A-Za-z0-9._-]{12,}` cannot satisfy `(?!\()`
by giving back a character: the character it would give back is in its own charset, which
the other half of the same lookahead forbids. So the engine backtracks all the way to the
floor, fails at every length, and emits nothing. The rule does not truncate the value or
mask part of it. It goes silent.

One thing the escalation understated. It read the `\(` half as load-bearing. It is not,
any more — wq#2297 replaced the label half with a shape test in which bare `key` and bare
`PAT` are no longer labels at all, so `key = apply_key_allowlist(reg)`, the wq#2284 line
that motivated the lookahead, is already refused one stage earlier. What the `\(` half
still catches is the narrower `<qualified noun> = <function>(` shape.

## The ruling: option 1, narrowed on the value's shape

The ticket offered three. Taking them in reverse order of how close they came:

**Option 2, accept and document, is refused.** The rule feeds instinct 4, where a hit is
an alarm and a miss is a credential in every downstream artefact — attempt notes, episode
store, PR text — unmasked and unflagged. Documenting a known silent miss in the alarm
layer is documenting that the alarm cannot be trusted, and a paren is not an exotic
neighbour: `print(f"TOKEN={tok}")`, a JSON blob cut at a value, a shell line, all produce
it. The whole ISSUE-LOG 568–599 cascade was a fight about false positives, and it ended by
overshooting into the one failure direction those rows do not cover.

**Option 3, mask and alarm but suppress the rotation escalation, is the right shape and
the wrong scope for this ticket.** It has the best failure direction — nothing is ever
silently missed, only de-escalated. But `masking.py` returns labels and records; the
escalation decision lives in `fworker`, so it needs a new record field, a consumer in
`write_mask_hits`/the rotation filer, and a third copy of the concept in the daemon, which
has no records at all (it returns labels and fails the verb). That is a change to the
alarm-routing contract, not to a regex, and it should not ride in on a regex fix.

**Option 1 is what shipped**, with one correction to how the ticket framed it. The ticket
suggested suppressing only when the value "looks like a snake_case/camelCase identifier".
Identifier *charset* is not the discriminator — `abcdefghijklmnop123456` is a perfectly
legal identifier. *Segmentation* is. A called name in real source carries a separator or a
hump; a credential body is an undifferentiated run. So the test moved off the character
after the value and onto the value itself:

- `harness/masking.py:92-96` — `_KV_SEGMENTED` / `_KV_HUMPED` / `_KV_NOT_CALLED`, applied
  at `harness/masking.py:158`.
- `token-engine/token_engine_daemon.py:112-114`, applied at `:133`.

`-` is deliberately in neither charset: no identifier contains one, so a dashed value glued
to a `(` reads as a credential. The hump test is `(?-i:)` under an IGNORECASE rule, the same
asymmetry `_KV_WORD` uses — `oauthToken` has a hump, `OAUTHTOKEN` does not.

## What it costs

`ops/wq2632-kv-paren/probe_paren.py`, over every `.py` under `harness/ dispatcher/ ops/
token-engine/`:

```
files scanned: 486
  shipped                36 hits
  wq2288                 36 hits
  no_call_test           53 hits
  shipped_plus_charset   36 hits
trailing charset half inert: True
paren-adjacent matches the wq#2288 `\(` half suppressed: 17  (all STILL SUPPRESSED)
hits the narrowing adds over the corpus: 0
```

Zero precision cost: identical corpus hit count, none of the 17 suppressions regained, no
new match anywhere. Every one of the 17 is `<label> = <segmented identifier>(` —
`read_oauth_token(`, `_read_gateway_token(`, `key_file.read_text(`, `_ebay_access_token(`.
The recall the narrowing buys is the whole opaque-value population, which the corpus
cannot show because the corpus is source code, so it is pinned as tests instead.

## Tests

`harness/tests/test_masking.py`:

- `test_an_opaque_value_glued_to_a_paren_still_alarms` — the regression itself. Written so
  it FAILS on the shipped wq#2288 rule (3 of 3 assertions, verified by re-running the body
  against a rebuilt old pattern), per wq#2630: a probe drawn only from the population the
  fix was designed around cannot falsify anything.
- `test_a_called_identifier_is_still_not_a_credential` — the six call shapes the probe
  found, so the narrowing cannot quietly give back what wq#2288 removed.
- `test_token_engine_mirror_agrees` now carries both lists, because the daemon copy had the
  same hole and there a miss does not even page: it hands the unmasked result back through
  the broker verb.

127 tests green: `harness/tests/test_masking.py` 67, `token-engine/tests/test_daemon.py` 60.
The rest of the mask suite: `test_burn_filter.py` + `test_token_injection.py` 53 green,
`test_mask_replay.py` 66 green and **1 pre-existing failure**,
`test_wq1819_transcript_burned_nothing`. Not from this change, and proved rather than
asserted: `replay(1819)` detects with `LEGACY_PATTERNS` and only feeds `masking.PATTERNS`
into the `still_matches_today` field, so both rules return the identical 3 records, all
with `still_matches_today=False`. The test pins `len(records) == 1` against a live corpus
that picked up a snapshot taken today. Recorded as ISSUE-LOG row 773 and filed as
**wq#2678**; re-pinning a burn-audit corpus changes what that test certifies and does not
belong under this ticket's gate.

## Left open

Option 3's de-escalation channel. The narrowing above still *suppresses* — any value that
happens to be segmented and paren-adjacent is silently dropped, which is the same failure
direction in a much smaller box — and so is every other suppression in the rule,
`_KV_POINTER` and `_KV_NOT_PUBLIC` included. The durable fix is for the alarm layer to
distinguish "matched, low confidence" from "did not match", so nothing in a
credential-shaped position is discarded without a record. That belongs with `fworker`'s
rotation filer, not with a regex, and is filed as **wq#2677** (`deps=2632`).
