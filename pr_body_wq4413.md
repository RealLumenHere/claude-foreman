# Judgment on the verify-pass contradiction of PR#2300 (ProviderSpec count)

Chain: wq#4351 → wq#4386 → wq#4401 (PR#2297) → wq#4404 (PR#2299) → wq#4406
(PR#2300) → wq#4408 (CONTRADICT) → this pass.

Refs: wq#4413

## What

- `research/WQ4351_NANOBOT_DISSECTION.md:98` (E5 errata row) and `:229` (§3
  superseded-note), `reports/wq4406_judgment_wq4404_nanobot_citations.md:68`:
  the `openai_compat` ride-count `39` → `38`.
- New `scripts/wq4413_count_nanobot_providerspecs.py`: derives every provider
  catalog figure from the sha256-pinned upstream `registry.py` and fails if
  either doc asserts an unsupported number.
- `reports/wq4408_verify_wq4406_providerspec_count.md:58`: a mislabel in the
  evidence file corrected (see below).
- `reports/wq4413_judgment_wq4408_providerspec_count.md`: the judgment record.
- `kb/ISSUE-LOG.md` row 1478 → SOLVED (mislabel corrected); new row 1479.

## Why

**The verify-pass is upheld.** PR#2300's new "39 of the 46 `ProviderSpec`
entries ride `openai_compat`" is miscounted. Derived from
`HKUDS/nanobot@909b277e` `nanobot/providers/registry.py`:

```
PROVIDERS tuple              : lines 215-943
catalog ProviderSpec entries : 46
ProviderSpec calls file-wide : 47 (1 outside the catalog: 969-979)
openai_compat 38 | anthropic 3 | azure_openai 1 | bedrock 1
openai_codex 1 | xai_grok 1 | github_copilot 1        (38+3+1x5 = 46)
```

The 47th `ProviderSpec(` construction is `create_dynamic_spec()`'s factory
default at `registry.py:969-979`, outside the `215–943` tuple. PR#2300 counted
it as a catalog member.

**Three sites, not the two the ticket named.** Line `:98` words the same ratio
as *"one of 39 such specs out of 46"*, which a grep for `"39 of the 46"`
misses — so a narrow patch would have left a stale number in the very errata
row it was fixing.

**The authoring method is the root fix.** Three straight judgment rounds each
corrected the prior round's hand-transcribed number and introduced a new one.
A fourth hand-patch had no reason to break the pattern.

## How

`scripts/wq4413_count_nanobot_providerspecs.py` fetches the rev-pinned raw
`registry.py`, checks its sha256, parses it with `ast`, and derives: the
`PROVIDERS` tuple's line span (`end_lineno`, not an asserted range), its
`ProviderSpec` entry count, the per-`backend` tally, the `ProviderSpec(` calls
outside the catalog, and the seven adapter names documented at `:71-72` (a
comment — `backend` is annotated plain `str`). It then requires seven exact
substrings, built from those derived numbers, in the two docs — each wording
pinned separately. `--registry PATH` runs it offline against a local copy.

```
$ python3 -I scripts/wq4413_count_nanobot_providerspecs.py
OK: 7 doc claims match the derived counts.
```

## Testing

Gate run against the pinned remote rev and a local copy: exit 0, seven claims
matched. Falsifiability (wq#2630) — the probe can fail, four ways, three
demonstrated:

1. **Pre-patch, real file** → exit 1, naming exactly the stale sites and
   nothing else.
2. **Mutation, outside the population the fix was designed around** — insert
   one synthetic `ProviderSpec(backend="openai_compat")` inside the tuple's
   closing paren → reports **47 / 39**, four claims fail. It follows the data
   rather than restating a constant.
3. **The catalog boundary itself** — `create_dynamic_spec()`'s spec is
   excluded by derivation from the tuple's own `end_lineno`. That is the
   boundary PR#2300 crossed.
4. A tampered or redirected fetch fails the sha256 check before counting.

One script artifact was caught, not papered over: v1 spanned the whole comment
block above `backend:` and derived `:70-72`, which would have flagged the
docs' *correct* `:71-72` citation as drift. The span now covers only comment
lines carrying a quoted adapter name. A gate that "fixes" correct docs to match
a sloppy derivation is worse than no gate.

Also corrected in the cited evidence:
`reports/wq4408_verify_wq4406_providerspec_count.md:58` said the file has "47
total `backend="openai_compat"`-style assignments" — it has 47
`ProviderSpec(` constructions and 39 such assignments. The arithmetic that
follows was right; the label was the same conflation the chain has been
chasing, so it is not left standing in the evidence file.

## Risk / rollback

Docs and one standalone script; no runtime code paths touched. The script is
not wired into CI, so a stale sha256 after an upstream force-push would fail
only when someone runs it deliberately. Rollback is `git revert` of this
commit.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
