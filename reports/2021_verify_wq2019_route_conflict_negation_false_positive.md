# wq#2021 — verify-pass: wq#2019 duplicate-route false-positive fix (PR#641)

**Verdict: CONTRADICT** — PR#641's `route_conflict()` fix narrows the old
co-occurrence-anywhere false positive correctly, but it is still pure proximity
matching (verb within ±60 chars of a litellm keyword + route name), not intent
parsing. A proposal that uses the word "add" for something else (caching, a
wrapper, a dashboard) in the same paragraph as an existing route name — including,
ironically, a proposal that explicitly says *"this doesn't add a new route"* to
preempt exactly the wq#2019 rejection reason — still gets hard-rejected as
`Duplicate: LiteLLM route "<name>" already exists`. Reproduced live against the
shipped module, not simulated.

PR#641, merged to main as `787d45c`. Shipped under
`ops/wq2019-proposal-reviewer-fix/` (scope note, same as every prior ticket in
this repo: not applied to live `/home/lumen/minerva` per the diff's own README —
not a defect, noted for scope only).

## What the diff does

`route_conflict()` (`minerva_dedup.py:134-154`): instead of flagging a conflict
on any co-occurrence of a litellm/gateway keyword and an existing route's
`model_name` anywhere in the proposal text, it now requires an add-intent verb
(`add|adding|create|creating|register|registering|onboard|onboarding|introduce|
introducing`), and only checks a ±60-char window around *each* verb match for
both a litellm/route keyword and a route name substring. `minerva_proposal.py`'s
`INVARIANTS`/`LUMEN_STATE_FALLBACK` hardware text was also updated to describe
the current MacPro6,1 pool + incoming BC-250 GPUs instead of the stale
Broadwell/no-GPU line — that half of the fix is correct and I found no issue
with it (it's judge-prompt text, not executable logic, and it accurately
reflects the hardware wq#2019 described).

## Verification performed (adversarial, cold read + live + probe)

1. Read the diff cold (`git show 787d45c`), not PR#641's own commit message or
   README framing.
2. Ran the shipped probe as-is: `test_route_conflict.py` → `OK: 4 false
   positives cleared, 2 true positives still caught`. Confirmed by code
   reading that only **1 of the 4** "false-positive" cases actually exercises
   the new windowing logic — the other 3 (`qwen36-local`, `bge-m3`,
   `claude-max-sonnet` cases) contain zero add-intent verbs at all, so
   `route_conflict()` returns `False` trivially, before the window/name check
   ever runs. The test suite's claimed coverage is weaker than its printed
   summary implies, which is what prompted a closer adversarial look at the
   one case that does exercise the fix.
3. **Constructed adversarial inputs shaped like real Minerva proposal text**
   (`title + proposed_change`, matching the exact call shape at
   `minerva_proposal.py:482-483`) and called `route_conflict()` directly
   against the shipped module with the same route config the shipped test
   uses:
   ```
   >>> route_conflict("Add semantic caching to the LiteLLM gateway route "
   ...     "table; this doesn't add a new model, just wraps existing route "
   ...     "bge-m3 with a cache layer.")
   (True, 'bge-m3')
   ```
   This is a technique proposal (caching, not a route add) — the exact class
   wq#2019 exists to stop rejecting — and it is still rejected, live, against
   the patched code. Re-run twice, deterministic both times.
4. Traced the call site (`minerva_proposal.py:479-491`): `route_conflict()`'s
   result alone (no OR with the semantic `check()` layer, no second opinion)
   sets `decision='reject'`, `decision_reason='Duplicate: LiteLLM route
   "bge-m3" already exists...'`, and short-circuits — `mark_processed()` +
   `continue` — before the proposal ever reaches the judge. There is no path
   by which this false positive gets caught downstream.
5. Checked whether this needs a contrived negation to trigger, or generalizes:
   tried a second shape without any negation, just proximity of an unrelated
   "add" to a route mention:
   ```
   >>> route_conflict("Introduce a small wrapper around the LiteLLM gateway "
   ...     "route logic so requests to qwen36-local get retried with backoff.")
   (False, None)
   ```
   This one and a couple of other phrasings I tried did *not* trigger — the
   ±60-char window is narrow enough that most realistic sentence-level
   distance between an unrelated verb and a route name misses it. The
   negation case (step 3) is the reliable trigger: writing out *why* something
   is not a route-add ("doesn't add a new model/route") reliably places a
   second, close-by "add" token right next to the keyword+route-name pair the
   window is scanning for. I did not find a way to break the 2 genuine
   true-positive cases in the shipped test — those still catch correctly.

## Why this matters (not a corner case)

- The trigger phrasing — explaining that a change is *not* a duplicate route
  add — is exactly the kind of self-justifying language an LLM-generated
  proposal is more likely to produce **now**, after wq#2019's own INVARIANTS
  changes push the judge (and, by extension, the generation prompt building
  `proposed_change`) toward being explicit about what a change does and does
  not do. The fix's own subject area makes its own failure mode more likely to
  fire, not less.
- The window logic has no negation handling, no dependency/constituency
  parsing, and no requirement that the matched verb's grammatical object be
  the route name or a litellm keyword — it is "these tokens appear within 120
  characters of each other," same class of string-proximity heuristic as the
  bug wq#2019 reported, just with a tighter radius. A radius narrows the
  false-positive rate; it does not close the bug class.
- `decision_reason` on the false-positive path is byte-identical to the reason
  string wq#2019 cites as the flagship symptom (`Duplicate: LiteLLM route
  "<name>" already exists in litellm_config.yaml`), so a recurrence would be
  invisible in the aggregate reject-reason stats `reopen_stale_rejects.py`
  uses to find and requeue wq#2019's original false positives — a fresh
  instance of this bug looks, to that script and to any future audit, exactly
  like a proposal that hasn't been touched yet.

## What a correct fix looks like (not implemented here per this archetype's instruction)

The window check needs to confirm the add-intent verb's target is the route
name itself, not just co-location — e.g. require the route name to appear
*after* the verb within a much shorter span (say, the same clause, split on
punctuation) and reject the match if a negation token (`not|doesn't|does not|
isn't|without|rather than|instead of`) appears between the verb and the route
name. Cheaper alternative in the same fail-open spirit as the rest of the
module: only match the *first* add-intent verb in the text (the one most
likely to state the proposal's actual action) rather than scanning every verb
occurrence, since restated/negated verbs later in the text are what produced
this false positive.

## Scope note

Same as every ticket in this chain: this fix lives in
`ops/wq2019-proposal-reviewer-fix/`, not `/home/lumen/minerva` — per the diff's
own README, the primary deploy path is `lumen-pr` fast-forwarding the live
tree, which per wq#2019's closing notes has not happened yet. Not evaluated:
whether `reopen_stale_rejects.py`'s dry-run count (212 rows, per wq#2019's
notes) is itself accurate — that requires live DB access outside this
worktree's granted dirs.
