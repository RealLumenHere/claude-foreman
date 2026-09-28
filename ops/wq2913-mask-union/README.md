# wq#2913 — union-of-spans masking

## The class

A stop condition can only SUBTRACT coverage, and nothing re-adds what it subtracted.

wq#2720 gave every fixed-prefix rule a foreign-prefix stop so one rule's greedy body
would not swallow the position where another rule's credential begins. The stop was
right about one thing and wrong about another, and the design used one mechanism for
both: it decided where a rule's match ENDED, which is a question about naming, and by
doing so it decided what got MASKED, which is a question about safety. Three tickets
patched the arithmetic (wq#2848 narrowed the stop to full shapes and pinned an
unguarded floor run; wq#2899 added a per-rule absorb for a neighbour's already-masked
residue), and the class survived all three: a full foreign shape sitting past a rule's
floor stopped the run, the foreign rule masked only its own shape, and the remainder —
right charset, no prefix of its own — had nothing left for any later pass to key on.

wq#2868 measured it at 200/200 trials on main AND on wq#2848's fix
(`kb/reports/wq2868_mask_stop_judgment.md`).

## The fix

Every rule reads the ORIGINAL text; the output redacts the UNION of what they found.

Coverage is then monotone in the rule table — at least every single rule's own
coverage — so it is fail-CLOSED by construction. There is no foreign-shape table to
keep in sync with PATTERNS, no floor arithmetic, and no pass cap.

Two things the bare union needed on top, both coverage-ADDING so the monotonicity
holds:

- **Phase 2, a re-read inside claimed spans** (`masking.read`). The leading word
  boundary refuses a prefix glued to an alphanumeric, because in ordinary text that is
  an identifier tail (`highs_solver`) and the false positive costs a rotation ticket.
  Inside a span another rule already read as credential body, the text is not ordinary,
  so the boundary is dropped there — which is what recovers wq#2702's glued slack body,
  the case a plain union over the original text misses.
- **A shorter `sk-` prefix group** (`masking._PRE_NOT_SK`). `sk-(?:[a-zA-Z]+-)*` is the
  one prefix group in the table that is not a fixed literal, so it is the one that can
  grow into the body — and a kept prefix is by definition what a mask prints. A
  qualifier unit may no longer contain the start of another rule's prefix.

And one thing it needed the other way, restoring today's alarm semantics rather than
widening them: `masking._redundant` drops a match whose value is entirely explained by
the matches inside it plus this table's own kept prefixes. That is wq#2298/wq#2638's
own-output screen applied to the text this pass is about to mask rather than to masking
already on the page. Without it, a git remote whose password is a github token files two
rotation tickets under two hashes for one credential.

The record a scan emits still stops where the next credential starts even though the
SPAN does not — the naming half of what the stop got right, kept, now that it can no
longer cost coverage.

## Mirrors

One implementation where that was possible and a faithful copy where it was not:

| mirror | before | after |
|---|---|---|
| `harness/masking.py` | the rules | the rules |
| `ops/wq2316-mask-secrets-boundary/mask-secrets` | ~30 sed substitutions re-expressing 9 rules in POSIX ERE, run 6 times over | a wrapper calling `masking.main` |
| `token-engine/token_engine_daemon.py` | copy | copy (it runs as `tokeneng`, off the harness import path, so it cannot import) |

The sed copy drifted on nearly every masking ticket and shipped a real leak to
`/home/lumen/bin` (wq#2726 -> wq#2732). Deleting it also deleted the three places it
was deliberately WIDER than masking.py; what that gave up is named in
`ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py`
(`test_the_dsn_rule_no_longer_diverges_from_masking_py` and its sibling). Re-widening
belongs in masking.py's own floors, where the tripwire gets it too.

## Gates

```
python3 ops/wq2913-mask-union/probe_mirrors.py          # 0 leaks, all 4 cases, all 3 mirrors
python3 -m pytest harness/tests/test_mask_mirror_coverage.py -q      # the differential corpus
python3 -m pytest harness/tests/test_masking.py harness/tests/test_mask_replay.py \
                  harness/tests/test_mask_foreign_prefix_matrix.py \
                  harness/tests/test_mask_underfloor_invariant.py -q
python3 -m pytest ops/wq2316-mask-secrets-boundary/test_mask_secrets_boundary.py -q
```

`probe_mirrors.py` takes a repo root, so it can be pointed at a revision that predates
this fix — the falsification the acceptance needs (wq#2630). Against `/home/lumen/foreman`
at `44aff37` it reports 200/200 leaked on both tail cases for all three mirrors.

## Deploy

`bin/mask-secrets` is installed from the wq#2316 bundle and `/home/lumen/bin` is a
primary tree a build worker may not write, so the install is its owner's step:

```
bash /home/lumen/foreman/ops/wq2316-mask-secrets-boundary/install.sh
```

Until that runs, the installed copy is still the sed program. The wrapper resolves its
own bundle's `harness/` first, so the repo's tests exercise the branch either way.
