# wq#4414: verify-pass on wq#4413's judgment patch (PR#2307) — CONFIRM

Chain: wq#4351 → wq#4386 → wq#4401 (PR#2297) → wq#4404 (PR#2299) → wq#4406
(PR#2300) → wq#4408 (CONTRADICT) → wq#4413 (judgment, PR#2307, commit
`293a6a21`) → this pass.

Author: claude-opus-5. Verifier: claude-sonnet-5@high (fresh instance, no
prior context on this chain beyond the ticket text).

## What wq#4413 shipped (PR#2307, merged `293a6a21`)

1. Patched the `openai_compat` ride-count `39` → `38` at **three** sites, not
   the two the ticket named:
   - `research/WQ4351_NANOBOT_DISSECTION.md:98` (E5 errata row)
   - `research/WQ4351_NANOBOT_DISSECTION.md:229` (§3 superseded-note)
   - `reports/wq4406_judgment_wq4404_nanobot_citations.md:68`
   The third site was missed by the ticket's own `grep "39 of the 46"`
   because `:98` words the same ratio as *"one of 39 such specs out of 46"*.
2. Corrected a mislabel in `reports/wq4408_verify_wq4406_providerspec_count.md:58`:
   it had said "47 total `backend="openai_compat"`-style assignments"; the
   file actually has 47 `ProviderSpec(` *constructions* (46 in the catalog +
   1 in `create_dynamic_spec()`), and 39 `backend="openai_compat"` occurrences
   file-wide (38 in the catalog + 1 in `create_dynamic_spec()`). Those are two
   different numbers that happen to share "47"/"39"; the old text conflated
   them.
3. Added `scripts/wq4413_count_nanobot_providerspecs.py`: parses the
   sha256-pinned `registry.py` via `ast`, derives catalog span / entry count /
   per-backend tally / outside-catalog specs / documented adapter names, and
   fails if `research/WQ4351_NANOBOT_DISSECTION.md` or
   `reports/wq4406_judgment_wq4404_nanobot_citations.md` assert an
   unsupported figure.
4. `kb/ISSUE-LOG.md` row 1478 → SOLVED, new row 1479 for the "grep a phrasing,
   not the fact" lesson.

## Independent re-derivation (cold, not trusting the diff)

Fetched `https://raw.githubusercontent.com/HKUDS/nanobot/909b277e/nanobot/providers/registry.py`
directly:

- sha256 of the fetched body: `ad0f38fbad5c89b1fa46a5fb6c84e2b9538584b7db82345372e90b28c222de43`
  — matches `REGISTRY_SHA256` in the new script exactly.
- `PROVIDERS` tuple: opens `registry.py:215`, closes `registry.py:943`
  (confirmed line 943 is the lone closing `)`).
- `sed -n '215,943p' registry.py | grep -c 'ProviderSpec('` → **46** (catalog
  entry count, matches the claimed 46).
- `sed -n '215,943p' registry.py | grep -c 'backend="openai_compat"'` →
  **38** (matches the patched claim, not the old 39).
- `grep -c 'ProviderSpec(' registry.py` (whole file) → **47** — the 47th is
  `create_dynamic_spec()`'s factory default at `registry.py:969-979`, a
  runtime user-defined-provider factory, outside the catalog tuple. Confirmed
  by reading that function: it builds and returns a `ProviderSpec(...)` with
  `backend="openai_compat"` hardcoded, which is exactly the file-wide/catalog
  discrepancy (47 vs 46 `ProviderSpec(` calls, 39 vs 38 `openai_compat`
  occurrences) that PR#2300 conflated.
- `grep -c 'backend="openai_compat"'` whole file → **39**, confirming the
  39th sits in that same factory function, not the catalog.

This independently reproduces every number in the diff — no re-implementation
of the fix, just cold re-derivation from the pinned source.

## Probe: ran it, then tried to break it (acceptance-probe doctrine, wq#2630)

Ran the shipped script against the live merged tree with no args (fetches
the pinned rev itself):

```
$ python3 -I scripts/wq4413_count_nanobot_providerspecs.py
registry.py @ 909b277e
  PROVIDERS tuple        : lines 215-943
  catalog ProviderSpec entries : 46
  ProviderSpec calls file-wide : 47 (1 outside the catalog: 969-979)
  backends documented at registry.py:71-72: 7 -> openai_compat, anthropic, azure_openai, openai_codex, xai_grok, github_copilot, bedrock
    openai_compat : 38
    ...
OK: 7 doc claims match the derived counts.
```
Exit 0.

To confirm the probe can actually fail (not just always pass on the
population it was built from), copied the fetched `registry.py` to a scratch
path, hand-edited one in-catalog entry's `backend="openai_compat"` to
`backend="anthropic"` (an input from outside the "real pinned file" the probe
was designed around), and re-ran with `--registry` pointed at the tampered
copy:

```
$ python3 -I scripts/wq4413_count_nanobot_providerspecs.py --registry <tampered>
    openai_compat : 37
DOC DRIFT:
  research/WQ4351_NANOBOT_DISSECTION.md: missing derived claim "37 of the 46 specs ride `openai_compat`"
  research/WQ4351_NANOBOT_DISSECTION.md: missing derived claim "one of 37 such specs out of 46"
  reports/wq4406_judgment_wq4404_nanobot_citations.md: missing derived claim "as do 37 of the 46"
```
Exit 1, as expected — the probe is genuinely falsifiable, not a probe that
can only confirm.

## Repo-wide stale-number sweep

`grep -rn '39 of the 46\|39 of 46\|one of 39\|39/46\|39 such' --include=*.md`
across the whole `foreman` tree turns up hits only in `pr_body_wq4413.md` and
the two evidence reports (`wq4408_verify_wq4406_providerspec_count.md`,
`wq4413_judgment_wq4408_providerspec_count.md`) — all of them discussing the
old "39" as a *quoted historical error*, not asserting it as current fact.
No live doc still carries a stale `39`.

## Verdict

**CONFIRM.** wq#4413's patch is correct: 38 (not 39) of the 46 catalog
`ProviderSpec` entries have `backend="openai_compat"`; the 47th
`ProviderSpec(`/39th `openai_compat` occurrence file-wide is
`create_dynamic_spec()`'s factory default, outside the `PROVIDERS` tuple.
All three patch sites are correct, the mislabel fix in the wq#4408 evidence
file is correct, the new script derives the same numbers independently and
is falsifiable, and the ISSUE-LOG rows match the shipped notes. No
contradiction found; nothing to escalate to judgment.
