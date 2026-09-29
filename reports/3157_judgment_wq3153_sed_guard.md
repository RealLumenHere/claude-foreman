# wq#3157 — judgment: wq#3153's kv-secret sed-pattern guard

**Ruling: wq#3156's contradiction is UPHELD. Fix-forward is the narrower guard
(true sed-substitution detection), landed now in both copies. No separate
"pull back pending a fix" step — the stricter form *is* the fix, so the interim
revert and the repair are the same commit.**

## What wq#3153 got right, and where it overreached

Right: the two burned values (`54b1b4739062`, `17e43898b34b`) really were sed
pattern-halves, not credentials. wq#3156 hash-reconfirmed that independently.
Suppressing them is correct and the guard's *reason* is sound.

The reason, in wq#3153's own words at `harness/masking.py:231`, was: "Refusing it
here loses nothing: the scan resumes at the replacement's own label, so a literal
written there still fires." That is a claim about a **two-sided** structure —
pattern half, then the label again on the replacement side. The regex it shipped,
`(?!\.[*+][#/@:!~%])`, tests only the **first three characters of the value** and
never checks that the second side exists. The guard was written wider than its own
justification.

Because `_KV_POINTER` is evaluated before the floor and is shared by `_KV_SECRET`
and the wq#2839 env-style `_KV_SECRET_ENV`, the blast radius was every
single-occurrence credential whose body happens to open with `.*` or `.+` plus one
of `# / @ : ! ~ %` — under either rule, at any length above the floor.

Reproduced here before touching anything
(`ops/wq3157-sed-guard/probe_sed_guard.py`, pre-fix run): `my_api_token = "<24
opaque>"` fires `['kv-secret']`; the same line with `.*#` in front of the body
fires `[]`. `API_TOKEN=.*#…`, `DB_PASSWORD=.+/…`, `SECRET_KEY=.*@…`,
`AUTH_TOKEN=.*:…` — all silent.

`masking.PATTERNS` has no generic high-entropy rule independent of a vendor prefix
or a kv label, so nothing backstops a body in that shape. The silence was the whole
distance between a leak and a rotation ticket. In `token_engine_daemon.py` it is
worse than missed paging: that copy is detect-only and fails closed, so a
suppression there stops the verb from FAILING and the unmasked body rides out on
the broker response (wq#2632's reasoning for the mirror).

This is ISSUE-LOG #709 / wq#3133 again: a suppression whose *premise* is a
structure, spelled as a *prefix*, with the untested boundary shipped as the only
uncovered case. wq#3153's single new test
(`test_a_literal_on_the_sed_replacement_side_still_fires`) exercises only the
two-sided shape — the one input where the over-broad guard and the correct guard
agree — so it could not have failed.

## The two options the ticket put up, and why one wins

*Floor/count-based tightening* (e.g. raise the floor, or require more wildcard
repetitions) was rejected. It re-tunes a threshold on a signal that does not carry
the information: a sed pattern half and a credential body starting `.*#` are the
same *length* and the same *charset*. Any floor that suppresses the real wq#3150
line also suppresses a long opaque body behind the same three characters, so the
bypass survives at a different size — the boundary moves, it does not close.

*Requiring the label to reappear* tests the exact premise the suppression was
argued from. It is falsifiable by construction: a line that does not repeat its
label cannot be a substitution rewriting that label.

## The change

`harness/masking.py` and `token-engine/token_engine_daemon.py`:

    (?!\.[*+][#/@:!~%|,]\^?(?P=kl)[:=])

The same label must appear on the replacement side, carrying its own `:`/`=` —
which is what a substitution rewriting `LABEL=value` writes. Per wq#3133's "every
repetition is COUNTED" the gap is zero: it is zero in both wq#3150 shapes
(`s#^LABEL=.*#LABEL=$TOKEN#`, `s/^LABEL=.+/LABEL=${NEW}/`), and every character of
slack there is a character a crafted value could sit in. Only an optional `^`
anchor is allowed through. Cost: one backreference, evaluated only where a label
and separator already matched, so it does not re-run the label grammar the way
`_KV_NOT_IDENT`'s first cut did (wq#2806); 16 KB of dense kv lines measures ~33 ms,
unchanged from pre-fix.

**Convergence, not duplication.** ISSUE-LOG #1063 shows wq#3154's lane — the
sibling rotation row for the same wq#3150 template — derived this exact guard
independently while this ticket was open. Two independent derivations landing on
one regex is the strongest available evidence for the ruling; the alternative, two
lanes shipping two dialects of the same suppression, is the failure that makes this
rule hard to reason about in the first place. This branch adopts wq#3154's form
verbatim, including its tighter bounds, in preference to the looser
`[^\n]{0,40}?` window first written here.

`|` and `,` in the delimiter class are **inert today** and recorded as such: neither
is in `_KV_BODY`, so the body stops at the delimiter and the match dies on the floor
whatever the guard says. They are parity with wq#3154 and insurance against a future
widening of `_KV_BODY`. They are deliberately not probed — a case that passes for a
reason unrelated to the thing under test is the wq#3153 mistake, not a fix for it.

The daemon's `_kv_rule` had a non-capturing `(?:label)` because no guard in that
copy backreferenced it; it is now `(?P<kl>label)`, matching `masking.py` since
wq#2933. It remains group 2 inside group 1, and the `\1` + MASK replacement shared
by every rule there is untouched.

## Evidence

- `ops/wq3157-sed-guard/probe_sed_guard.py` — two-sided A/B, runtime-assembled
  literals, no live file. Pre-fix: 5 bypass cases FAIL, 2 sed cases pass. Post-fix:
  10/10 `PASS`. The suppress half means a plain revert of wq#3153 fails this probe,
  so "always fire" is not a passing answer.
- `harness/tests/test_masking.py` — two new tests covering the boundary wq#3153
  shipped untested, one per rule (`_KV_SECRET` across all seven sed delimiters,
  `_KV_SECRET_ENV` across three env labels), each with the no-prefix A/B control
  inline. 167/167 pass (165 before).
- `harness/tests/test_mask_mirror_coverage.py` — the differential corpus test that
  compares the two copies, passing with the daemon's new named group.

## Follow-on, not blocking

wq#3153's two `KV_FALSE_POSITIVES` entries still pass, but both are two-sided by
construction. Worth a separate ticket: `masking.PATTERNS` has no entropy-based
fallback for a non-vendor-prefixed body, which is why *any* `_KV_POINTER` overreach
converts directly into silence. That is a structural single point of failure, not a
wq#3153 defect.
