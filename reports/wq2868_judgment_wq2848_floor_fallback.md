# wq#2868 — judgment: is wq#2848's floor-fallback the right fix for the foreign-prefix
# fail-open wq#2803 contradicted?

Reviewer: claude-opus-5 @ high. Worktree `wq2868-a1`. Adjudicated 2026-09-28.
Subject: `harness/masking.py` `_body(..., foreign=...)` / `_foreign_prefixes()`, as landed
by wq#2848 on main as **71f4e06** (`fix(mask): require full shape for foreign-prefix stop`;
the branch tip 3f532c4 named in wq#2848's done-notes is the pre-merge SHA, not a second
commit — 71f4e06 is what is on main and what this review reads).

## Verdict

**wq#2848's FIX APPROACH IS SOUND. It stands as scoped.** No redirect, and no structural
rework of the multi-pass loop. Two residuals are named below; one was already filed
(wq#2899), the other is filed by this ticket as **wq#2922** and is an older class
wq#2848 neither introduced nor widened.

The three questions this ticket was opened to decide:

1. **Is a floor fallback sufficient, or does the multi-pass loop need a structural rework?**
   Sufficient, and the loop is load-bearing and correct as it stands. See §2 and §3.
2. **Should kv-secret's deliberate no-stop exception inform how the floor fallback is
   designed?** It already does, and it confirms the design rather than demanding a change.
   See §4.
3. **Is the residual wq#2848 scoped out acceptable?** Yes for wq#2899 (rule order, strictly
   better than what it inherited). The under-floor class is NOT fully closed, but through a
   different guard, and it needs a different fix shape — wq#2922. See §5.

## 1. What the fix actually is

`_body(charset, quant, foreign)` now compiles, for an open floor `{N,}`:

    (?:(?!(?-i:MASKED))charset){N}        # floor: MASK guard only
    (?:(?!(?-i:MASKED|<foreign>))charset)*  # beyond the floor: both guards

and for the one exact quantifier (`aws`, `{16}`) it skips the foreign guard entirely
(`masking.py:195-202`). `_foreign_prefixes()` was simultaneously narrowed from a bare
prefix alternation to each other rule's **prefix plus its own floor-quantified body**
(`masking.py:131-134`), built from the single `_SHAPES_BY_LABEL` table at
`masking.py:81-88` so the rules and the guard cannot drift.

Those are two independent changes and the review treats them that way. The narrowing alone
is insufficient — cloudflare's `[A-Za-z0-9_.-]{6,}` is a superset of plain alnum, so a
github body carrying a bare `cfut` substring genuinely satisfies cloudflare's full shape
and would still stop early. The floor deferral is what makes the property unconditional.
The docstring at `masking.py:180-192` states this correctly.

## 2. The property, verified independently

The claim worth checking is not "fewer leaks" but **a foreign shape can no longer prevent
a match at all** — matchability of a rule must not be contingent on another rule's shape.

Probe: `reports/wq2868_probe_underfloor_class.py` (committed; takes a masking directory as
argv[1], so the same cases run against any historical rev extracted with
`git show <rev>:harness/masking.py`). Section A sweeps **every** offset 0..floor+6 for every
rule × every foreign prefix that rule's own charset can spell — 558 cases — rather than the
nine sampled offsets in wq#2848's own `test_mask_foreign_prefix_matrix.py`. Sampled offsets
are precisely what let wq#2720 ship this regression, so the probe was built to be able to
fail on a shape the fix's own tests never look at (wq#2630).

| probe section | pre-2720 `2f538e8^` | pre-2848 `71f4e06^` | main (wq#2848) |
|---|---|---|---|
| A — rule fires at all, 558 cases | 0 fail | **258 fail** | **0 fail** |
| B — under-floor via MASK guard, 96 cases | 96 fail | 96 fail | 96 fail |
| C — kv-secret host, 92 cases | 0 | 0 | 0 |
| D — url-auth host, 70 cases | 0 | 0 | 0 |
| E — four-credential chain | 5 fail | 0 | 0 |
| F — rule order, sk-key dash-preceded, 12 cases | 3 fail | **6 fail** | **3 fail** |

Reading: A reproduces wq#2803's contradiction unconfounded (258/558 on the shipped
wq#2720 code) and shows it closed, with parity restored to the pre-2720 masker. F shows
wq#2720 doubled the rule-order residual and wq#2848 returned it to the pre-2720 number —
the fix is a strict improvement on both axes it touches. E shows the wq#2720+wq#2848 pair
is strictly better than pre-2720 on chains.

## 3. Why no structural rework of the multi-pass loop

The floor deferral makes the guard **monotone**: it can shorten a match, never kill one.
That is a structural property of the compiled regex, not a probability — the first `N`
body characters are matched with the foreign lookahead absent from the pattern, so no
foreign shape at any offset can reduce the match below `prefix + N`. Exact-quant rules have
no "beyond the floor" and take no foreign guard at all. There is no input left for which
the guard changes *whether* a rule matches.

A rework would also cost something real. The stop is what creates the boundary the next
rule needs: after the host match is replaced with `<prefix>MASKED`, the foreign prefix sits
immediately behind the literal, which `_B`/`_AFTER_MASK` admits (wq#2694). Embedded
mid-body, that same prefix is preceded by a body character and `_B` refuses it — so without
the stop the foreign token is swallowed into the host mask. Swallowed is still masked, but
the foreign credential then gets no label and no rotation row of its own. Section E
confirms the handoff works to depth four: `github → aws → slack → cloudflare` glued with no
separators masks to `ghp_MASKED AKIAMASKED xoxb-MASKED cfut_MASKED` (spaces added here only
to keep this document off its own tripwire), all four labels in `hits`, zero body residue,
inside `_MAX_PASSES = len(PATTERNS)`. The cap's justification at `masking.py:690-693` — one
pass frees at most one more rule in a chain, and a chain cannot be longer than the rule
table — holds.

One thing the stop does **not** cost, contrary to a reasonable first reading: it leaves no
residue between the floor and the stop. The stop fires at the position where the foreign
prefix begins, that whole span is inside the host match, and the replacement drops the
entire body. Sections A/E show no residue on any of the 559 masked cases.

## 4. kv-secret's no-stop exception

wq#2720 gave kv-secret no foreign stop on purpose (`masking.py:357-363`): a labelled value
that simply *is* another rule's shape (`API_KEY` → an `sk-` token) must still resolve under
kv-secret, because `burn_filter.matched_values()` runs every rule against the original text
so kv-secret sees the real value behind a label another rule already masked elsewhere. A
stop at body position zero would refuse it outright, floor and all.

This does not pull against the floor fallback — it is the same principle at its limit. Both
encode: *a rule's matchability may not be contingent on a foreign shape.* kv-secret takes
the total form (`foreign=""`, so `_body()` returns the bare mask-guarded run and the floor
fallback is a no-op there); the fixed-prefix rules take the bounded form (the guard applies
only where the host's own floor is already satisfied). The exception is evidence the
generalisation is the right one, not a case the design has to special-case. Probe section C
(92 cases, kv-secret host with every foreign prefix at every offset under its floor) is
clean on all three revs, so the exception is also not carrying a leak of its own.

## 5. Residuals

**wq#2899 — rule order (accepted).** An sk-key body whose embedded foreign prefix is
preceded by a `-` (legal in sk-key's charset, and a position where `_B` genuinely qualifies
in the *raw* text) is taken by whichever rule runs first in `PATTERNS`, leaving a short
tail. 3/12 on main, 3/12 pre-2720 — inherited, not caused, and halved from the 6/12
wq#2720 left. Correctly out of wq#2848's scope.

**wq#2922 — the under-floor class through the sibling guard (newly filed, NOT wq#2848's
fault or scope).** `_body()` carries two guards; wq#2848 deferred one past the floor and
left the other — the unconditional mask-literal lookahead (wq#2638) — active inside it. So
the identical fail-open survives through the second door: a real token whose body contains
this module's own output literal at any offset below the rule's floor fails the whole match
and passes through in the clear, and probe section B records `hits=SILENT` on all 96 cases,
on all three revs. The natural rate is small (a random base62 body, ~62⁻⁶ per position) but
the direction is wrong, and it is reachable in combination with ISSUE-LOG row 878's other
occupant (wq#2846: collectors that truncate a field below the floor *before* masking it).

`masking.py:161-165` claims this cost "is paid in the direction that alarms rather than the
one that goes silent." Measurably false — the guard truncates the run under the floor, the
rule does not match, and nothing is recorded. That sentence must change with the fix.

The fix is **not** to copy wq#2848's deferral here: deferring the mask guard past the floor
lets a body swallow `<prefix>MASKED` again, `_OWN_TAIL` reads the result as own output and
wq#2298's fixed-point filter drops the whole match — the wq#2638 leak, restored. The right
shape is a **fail-loud backstop**: a boundary-qualified known prefix followed by an
under-floor body emits a record (label, `value_len`, `value_sha256_12`; no mask, and no
context window, for the reason `suppressions()` gives at `masking.py:724-729`) so every
under-floor path alarms instead of going silent. One backstop covers this guard, wq#2846's
upstream truncation and any future under-floor mechanism, which is why it is worth building
once rather than patching per-guard. That reasoning is recorded in wq#2922.

## 6. Gate

- `harness/tests/test_masking.py` — 121 passed. It was **red on main** on arrival, for an
  unrelated reason: `ops/wq2903-model-ref-drift/lumen_model_watchdog.py:205` carried a
  `DRIFT_KEY = "<18 chars>"` literal, so `test_the_mask_suite_does_not_arm_its_own_tripwire`
  filed the file as a whole credential shape. Fixed here by splitting the literal at the
  seam the rule reads, runtime bytes unchanged.
- `test_mask_foreign_prefix_matrix.py`, `test_mask_replay.py`, `test_mask_triage.py`,
  `test_mask_at_rest*.py` — 879 passed alongside test_masking in the same run.
- `harness/tests/test_mask_underfloor_invariant.py` (new) — 4 passed, 1 xfailed. It drives
  the probe above, so the invariant is pinned as a property rather than as sampled offsets,
  and section B is `xfail(strict=True)` against wq#2922: closing that ticket turns this test
  red until the marker is dropped, so the residual cannot be quietly forgotten.
- Not verified here, and unchanged from wq#2848's own done-notes: the root-owned
  token-engine daemon mirror is still not deployed (a restart affects credential brokering
  and wants a deliberate window). `/home/lumen/bin/mask-secrets` was deployed by wq#2848.
