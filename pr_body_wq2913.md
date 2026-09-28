wq#2913

## What

Masking no longer sweeps the rule table over its own output with stops in it. Every rule
now reads the **original** text and the output redacts the **union** of the spans they
found. `_foreign_prefixes`, `_foreign_bare_prefixes`, `_SHAPES_BY_LABEL`,
`_floored_absorb`, `_QUANT_RE`, `_MAX_PASSES` and `_sub` are gone; `_body()` keeps only
the wq#2638 MASK-literal guard.

The two other copies of the house rule set follow: `bin/mask-secrets` stops being a sed
re-implementation and becomes a wrapper around `masking.main`, and the token-engine
daemon's copy gets the same engine.

## Why

**A stop condition can only subtract coverage, and nothing re-adds what it subtracted.**

wq#2720 gave every fixed-prefix rule a foreign-prefix stop so one rule's greedy body
would not swallow the position where another rule's credential begins. That answered two
different questions with one mechanism: where a match *ends*, which is about naming, and
what gets *masked*, which is about safety. Three tickets patched the arithmetic — wq#2848
narrowed the stop to full shapes and added an unguarded floor run, wq#2899 added a
per-rule absorb for a neighbour's already-masked residue — and the class survived all
three. A full foreign shape past a rule's floor stopped the run, the foreign rule masked
only its own shape, and the remainder (right charset, no prefix of its own) had nothing
left for any later pass to key on.

wq#2868 measured it at **200/200 trials on main and on wq#2848's own fix**
(`kb/reports/wq2868_mask_stop_judgment.md`).

Under a union, coverage is monotone in the rule table — at least every single rule's own
coverage — so it is fail-closed by construction, with no table to keep in sync with
`PATTERNS`, no floor arithmetic and no pass cap.

## How

Two additions the bare union needed, both coverage-**adding**, so monotonicity holds:

- **`read()` phase 2** re-reads inside spans phase 1 already claimed, with the leading
  word boundary dropped. That boundary refuses a prefix glued to an alphanumeric because
  in ordinary text it is an identifier tail (`highs_solver`, wq#2292/wq#2400) — but
  inside a span another rule already read as credential body, a prefix is a prefix. This
  recovers wq#2702's glued slack body, which a plain union over the original text misses:
  github's `[A-Za-z0-9]` run swallows the letters of the slack prefix, so the slack rule
  sees it preceded by an alphanumeric and declines.
- **`_PRE_NOT_SK`**: `sk-(?:[a-zA-Z]+-)*` is the one prefix group in the table that is
  not a fixed literal, so it is the one that can grow into the body — and a kept prefix
  is what a mask prints. A qualifier unit may no longer contain the start of another
  rule's prefix. Without it the letter-fill matrix cases printed 1–5 body characters.

And one the other way, which *restores* today's alarm semantics rather than widening
them: **`_redundant()`** drops a match whose value is entirely explained by the matches
inside it plus this table's own kept prefixes. That is wq#2298/wq#2638's own-output screen
applied to the text this pass is about to mask rather than to masking already on the page.
Without it, a git remote whose password is a github token files two rotation tickets under
two hashes for one credential.

A record's reported **value** still stops where the next credential starts even though its
**span** does not — the naming half of what the stop got right, kept now that it can no
longer cost coverage. That is what keeps `mask_replay` reproducing a historical run's
recorded hashes instead of a composite no rotation worker could match a candidate against.

Mirrors — one implementation where that was possible, a faithful copy where it was not:

| mirror | before | after |
|---|---|---|
| `harness/masking.py` | the rules | the rules |
| `ops/wq2316-mask-secrets-boundary/mask-secrets` | ~30 sed substitutions re-expressing 9 rules in POSIX ERE, run 6× | a wrapper calling `masking.main` |
| `token-engine/token_engine_daemon.py` | copy | copy — it runs as `tokeneng`, off the harness import path, so it cannot import |

The sed copy had to re-express every lookaround structurally (mask literal as a sentinel
byte, foreign stop as a second sentinel, wq#2899's absorb as one more substitution per
rule). It drifted on nearly every masking ticket and shipped a real leak to
`/home/lumen/bin` (wq#2726 → wq#2732).

## Testing

- `python3 ops/wq2913-mask-union/probe_mirrors.py` — **0/200 leaks on all four cases for
  all three mirrors** (`OK - total leaked trials: 0`).
- **The probe can fail** (wq#2630): pointed at the main checkout `/home/lumen/foreman` at
  `44aff37` it reports **200/200 leaked, longest surviving run 9**, on both tail cases for
  all three mirrors — matching wq#2868's measurement. That input is a different repo root
  at a named revision, outside the scope the fix was written around.
- `harness/tests/test_mask_mirror_coverage.py` — the differential corpus asked for: 23
  lines through all three mirrors, byte-compared, plus a both-directions check so byte
  equality cannot pass vacuously. **47 passed.** Also falsifiable: standing the
  pre-wq#2913 masking.py in as one mirror diverges on corpus lines 6, 7 and 10.
- `test_masking.py` **130 passed**; `test_mask_secrets_boundary.py` **110 passed**;
  `test_mask_foreign_prefix_matrix.py`, `test_mask_underfloor_invariant.py`,
  `test_burn_filter.py` green; `test_mask_replay.py` **89 passed / 1 pre-existing failure**.
- Every file this branch touches reads silently through `masking.scan` — this change's own
  text arms no rotation row.

## Risk / rollback

Single revert: the three mirrors change together in this branch and nothing outside them
depends on the removed internals (`mask_text`, `scan`, `suppressions`, `raw_matches`,
`_matches`, `deployed`, `untruncated*` are all unchanged in signature).

Accepted cost, stated because it changes alarm volume: where two rules read the same
characters (`token: <github token>` is a github token *and* a kv-secret value) both labels
fire and the merged span redacts the wider of the two, so the inner rule's kept prefix
goes with it. More masking and more alarms, never less.

Deleting the sed copy also deleted the three places it was deliberately *wider* than
`masking.py` — an under-floor DSN password, prose `pass`, `PUBLIC_KEY=` — so those now
print through the pipe where the sed copy redacted them. Stated in
`test_the_dsn_rule_no_longer_diverges_from_masking_py` and its sibling. Re-widening belongs
in `masking.py`'s own floors, where the tripwire benefits too, not in a second copy of the
table.

Notes for the reviewer:

- **The base was stale.** `4009120` was an ancestor of `origin/main`, which had landed
  three fixes filed by this ticket's *own earlier attempt* (`203440b` kv-secret self-name,
  `05e65ce` url-auth placeholder, `322ff41` inert matrix ids). `origin/main` is merged in
  (clean) and all three guards are verified present and green. ISSUE-LOG rows 930/931.
- **Two red gates are not from this change.** `test_wq2913_corpus_burned_nothing` and
  `test_no_tracked_module_pages_when_a_worker_reads_it` fail identically against
  `/home/lumen/foreman` at `9ecd860`; filed as **wq#2959**. A third in that group — a
  `len(values) == 8` assertion against a 7-entry fixture — is fixed here, since it pins
  this ticket's own values.
- **`bin/mask-secrets` is not installed by this PR.** `/home/lumen/bin` is a primary tree
  a build worker may not write; the owner's step is
  `bash ops/wq2316-mask-secrets-boundary/install.sh`. Until then the installed copy is
  still the sed program. The wrapper resolves its own bundle's `harness/` first, so the
  repo's tests exercise the branch either way.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
