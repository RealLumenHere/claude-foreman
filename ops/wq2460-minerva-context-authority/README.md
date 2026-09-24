# wq#2460 — context-authority validation in the Minerva verify path

Proposal e90e6edcc2db (factory lane YELLOW, plan 6f56c415706b), grounded in
arXiv:2609.27603 "When Context Misleads: In-context Learning with Jurisdiction
in Large Language Models".

## Problem

The verify/judge path scores proposals for grounding and novelty, but nothing
tests whether a cited premise *should govern* the answer: a stale or misleading
premise in the intel item is treated as authoritative, and such proposals only
die later, at build, for stale/duplicate premises.

## Why not the factory draft diff

Plan 6f56c415706b's draft was rejected on review and reimplemented here:

- It SELECTed `fact, value, ts FROM state_facts` — the real bi-temporal schema
  (lumen_state.py) is `subject/predicate/value/note/valid_from/valid_to/...`;
  the draft's query raises OperationalError on first call.
- It read an `episode_facts` table that does not exist anywhere; the episodic
  store's interface is `lumen_episode.recall()`.
- Its functions were never wired into `verify()` — dead code, no prompt edit,
  despite the proposal specifying both.

## Change (`minerva_verify.py`, additive)

- `classify_premise_authority(premise, fact_rows, recall=None)` —
  AUTHORITATIVE: matches an open state fact (`valid_to IS NULL`) or an episode
  hit with score ≥ `VERIFY_AUTHORITY_EPISODE_T` (0.60) younger than
  `VERIFY_AUTHORITY_TTL_S` (7 d). STALE: matches only closed facts or only
  aged-out episodes. UNVERIFIED: neither store knows it. Matching reuses
  `_kw_claim_support` (same 0.5 content-word rule as the citation fallback).
- `context_authority_labels(claims)` — the validator entry point; fail-OPEN
  `(None, err)` when disabled or the store is unreadable.
- Judge prompt edit: `_judge_once(..., authority_note=)` inserts a CONTEXT
  AUTHORITY CHECK block naming stale/unverified claims and instructing the
  judge that such a premise must not govern its label (the paper's
  jurisdiction failure). Empty note = byte-identical prompt to before.
- Confidence cap in `verify()`: any non-AUTHORITATIVE premise caps
  `final_score` at `VERIFY_AUTHORITY_CAP` (default `FINAL_THRESHOLD`, 0.70).
  At the default the cap can never flip `passed` (min(x, T) ≥ T iff x ≥ T);
  set the cap below the threshold to make it a hard gate (reasserted past the
  latent-rescue layer, like the wq#2430 stale gate). Result rows gain
  `premise_authority` + `authority_capped`; no DB schema change.

Kill switch: `VERIFY_AUTHORITY_ENABLED=0`.

## Verification

- `python3 test_wq2460_context_authority.py` — 20 offline tests (no network,
  no live DB/services): classifier semantics incl. episode TTL + fail-open,
  entry-point fail-open, prompt-block content, prompt threading into the judge
  payload, and 4 end-to-end `verify()` wirings (cap w/o pass flip, no-op on
  all-authoritative, no-op on failed check, sub-threshold hard gate).
- `health_check.py` (run by install.sh after apply): live import, live
  `state_facts` schema satisfies the new SELECT, classifier smoke, signature
  back-compat.

## Rollback

`install.sh rollback` (restores `/home/lumen/backups/wq2460/minerva_verify.py.bak`),
or `VERIFY_AUTHORITY_ENABLED=0` to disable in place.
