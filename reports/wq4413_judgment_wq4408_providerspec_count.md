# wq#4413 — judgment on wq#4408's CONTRADICT of wq#4406 (ProviderSpec count)

Chain: wq#4351 (dissection) → wq#4386 (verify, CONTRADICT: `pip install
nanobot`) → wq#4401 (judgment, PR#2297/`2f5a2a80`) → wq#4404 (verify,
CONTRADICT: two citations "fabricated", PR#2299/`156996d0`) → wq#4406
(judgment, PR#2300/`dba71a0e`) → wq#4408 (verify, CONTRADICT: "39 of 46"
miscounted) → this pass. Judge: claude-opus-5@high, fworker-wq4413.

## Decision

**Both options.** wq#4408's contradiction is upheld — 38, not 39 — so the
in-place patch lands. But the ticket's second option is the one that matters,
and declining it would have been the fourth consecutive round of the same
mistake: every numeric claim in these two docs about the provider catalog is
now **derived** by `scripts/wq4413_count_nanobot_providerspecs.py`, which
parses the rev-pinned `registry.py` with `ast` and exits 1 if either doc
asserts a figure the file does not support. The script is the acceptance
gate for this ticket and the authoring method for any future numeric claim
here.

Narrow-patch-only was tempting (wq#4408 re-verified everything else clean, so
one digit was the whole known defect) and it is exactly what the last three
rounds did. Three rounds, three new transcribed numbers. The cost of a
derivation is ~170 lines, once.

## wq#4408's contradiction: upheld, and it undercounted the damage

Derived from `HKUDS/nanobot@909b277e` `nanobot/providers/registry.py`
(sha256 `ad0f38fb…de43`, pinned in the script):

```
PROVIDERS tuple        : lines 215-943
catalog ProviderSpec entries : 46
ProviderSpec calls file-wide : 47 (1 outside the catalog: 969-979)
backends documented at registry.py:71-72: 7
  openai_compat : 38      anthropic      : 3
  azure_openai  : 1       bedrock        : 1
  openai_codex  : 1       xai_grok       : 1
  github_copilot: 1
```

38 + 3 + 1×5 = 46, so the tally is self-checking. wq#4408 is right on both
the number and the mechanism: the 47th `ProviderSpec(` construction sits at
`registry.py:969-979` inside `create_dynamic_spec()`, outside the `215–943`
tuple, and PR#2300 counted its `backend="openai_compat"` default as a catalog
member.

**Three sites, not two.** The ticket (and wq#4408) names two — the E5 errata
row and the §3 superseded-note. There were three: a `grep "39 of the 46"`
misses `research/WQ4351_NANOBOT_DISSECTION.md:98`, which words the same ratio
as *"one of 39 such specs out of 46"*. That near-miss is the argument for the
scripted gate in one line: the error was found by grepping for a phrasing, and
the phrasing varied. All three are patched; the script pins both wordings.

## Two corrections to the evidence files themselves

- `reports/wq4408_verify_wq4406_providerspec_count.md:58` said `registry.py`
  has "47 total `backend="openai_compat"`-style assignments across the file".
  It has 47 `ProviderSpec(` constructions and 39 file-wide
  `backend="openai_compat"` assignments. The arithmetic that follows (47 − 1
  factory = 46 catalog) is right; only the label was wrong. Corrected in
  place.
- `kb/ISSUE-LOG.md` row 1478 inherited the same mislabel; corrected, status
  flipped to SOLVED.

Neither changes wq#4408's verdict. Both are the same conflation the whole
chain has been chasing, which is why they are not left standing in the file
that is cited as the evidence.

## The gate

`scripts/wq4413_count_nanobot_providerspecs.py` derives, from the pinned
`registry.py` alone: the `PROVIDERS` tuple's line span, its `ProviderSpec`
entry count, the per-`backend` tally, the count of `ProviderSpec(` calls
outside the catalog, and the seven adapter names documented at `:71-72`
(a comment, not a `Literal` — `backend` is annotated plain `str`). It then
requires seven exact substrings, built from those derived numbers, to be
present in the two docs.

```
$ python3 -I scripts/wq4413_count_nanobot_providerspecs.py
…
OK: 7 doc claims match the derived counts.      # exit 0, after the patch
```

### Falsifiability (wq#2630)

The probe can fail, three ways, all demonstrated:

1. **Pre-patch, against the real file** — exit 1, naming exactly the two
   stale-wording sites and nothing else (the third was found and patched
   before this run; its claim is pinned now):
   `missing derived claim "38 of the 46 specs ride openai_compat"`,
   `missing derived claim "as do 38 of the 46"`.
2. **Mutation, outside the population the fix was built around** — insert one
   synthetic `ProviderSpec(backend="openai_compat")` immediately inside the
   tuple's closing paren and the counter reports **47 / 39** and fails four
   claims. It follows the data; it does not restate a constant.
3. **The catalog boundary itself** — the `create_dynamic_spec()` spec at
   `969-979` is classified as outside the catalog by derivation from the
   tuple's own `end_lineno`, not by an asserted line range. That boundary is
   the one PR#2300 crossed.

A fourth failure mode is covered by construction: a tampered or redirected
fetch fails the sha256 check before any counting.

One script artifact was caught and fixed rather than papered over: the first
version spanned the whole comment block above `backend:` and derived
`:70-72`, which would have made the docs' correct `:71-72` citation look like
drift. The span now covers only the comment lines carrying a quoted adapter
name. A gate that "fixes" correct docs to match a sloppy derivation is worse
than no gate.

## Applied

- `research/WQ4351_NANOBOT_DISSECTION.md:98` (E5 errata row), `:229` (§3
  superseded-note): `39` → `38`.
- `reports/wq4406_judgment_wq4404_nanobot_citations.md:68`: `39` → `38`.
- `reports/wq4408_verify_wq4406_providerspec_count.md:58`: mislabel corrected.
- `scripts/wq4413_count_nanobot_providerspecs.py`: new derivation gate.
- `kb/ISSUE-LOG.md` row 1478: mislabel corrected, SOLVED.

## Verdict

**wq#4408 UPHELD.** One digit wrong in three places, now right and now
derived. The chain's defect class — a number transcribed by hand from a file
the author had open — has a gate in front of it for these two docs; run the
script before changing any count in them.
