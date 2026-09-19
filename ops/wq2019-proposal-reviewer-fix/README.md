# wq#2019 — Minerva proposal reviewer: stop killing real ideas

Frank: "every single thing on proposals gets deferred." Diagnosed 2026-09-18: reject
683 + rejected_aged 278 = 66%, defer 268 (18%), only ~33 approved (~2%). Two
root-cause bugs in the reviewer's decision logic, both in the live `/home/lumen/minerva`
tree (not this repo — see "Deploy" below).

## Bug 1 — duplicate-route false-positive

`minerva_dedup.route_conflict()`, called from the dedup gate in
`minerva_proposal.py` (`_conflict, _route = minerva_dedup.route_conflict(...)` ->
`decision = 'reject'`, `decision_reason = 'Duplicate: LiteLLM route "<name>" already
exists...'`), flagged a conflict on **any** proposal whose text contained a generic
litellm/gateway keyword (`litellm|model.list|gateway route|model pool`) **anywhere**
plus an existing route's `model_name` as a substring **anywhere** in the
title+proposed_change blob — no check that the proposal's actual change was adding
that route. Since virtually every Lumen proposal mentions "the LiteLLM gateway" (all
inference goes through it) and often names an existing local model by name (to use
it, not re-add it), this rejected real technique proposals: prompt caching,
quality routing, memory orchestration citing `glm-5.3-flash-local`, `qwen36-local`,
`bge-m3`, `claude-max-sonnet`.

**Fix** (`minerva_dedup.py`): a conflict now requires an explicit add/create/
register/onboard/introduce intent verb, with the route name inside the same local
text window (±60 chars) as that verb and a route/model keyword — not co-occurrence
anywhere in a multi-hundred-word proposal.

**Fix, round 2** (wq#2022, adjudicating wq#2021's CONTRADICT): the ±60 window alone
was still proximity, not intent. A proposal that *disclaims* a route add — "…this
doesn't add a new model, just wraps existing route `bge-m3`…" — put a second `add`
within 60 chars of both a keyword and a live `model_name` and was hard-rejected,
the exact class of proposal wq#2019 exists to protect. The window is now clipped to
the verb's own clause, the route name must *follow* the verb inside it, and a
negation (`not|doesn't|isn't|without|rather than|instead of|…`) anywhere inside that
span, on either side of the verb, kills the match. See `test_route_conflict.py`: 8
false-positive shapes clear (5 of which actually reach the window logic — wq#2021
noted the original 4 included 3 that returned early on no verb at all), 4 genuine
duplicate-route-add proposals still caught, including one behind a negated clause.
Full adjudication: `reports/2022_judgment_wq2021_contradict_wq2019_route_conflict.md`.

## Bug 2 — stale hardware-infeasibility

`minerva_proposal.py`'s `INVARIANTS` block (unconditionally appended to every judge
call regardless of the live hardware probe) hardcoded: "Xeon E5-2699 v4 (Broadwell)
— NO AVX-512 and NO AMX. Anything requiring AVX-512, AMX, or a CUDA GPU is
infeasible here; say that plainly and reject rather than defer." `LUMEN_STATE_FALLBACK`
carried the same "no dedicated GPU" claim. That was true when the fleet was a single
CPU-only Dell T5810; Frank has since added a 7x MacPro6,1 pool (E5-2697 v2, 24
threads, 64GB RAM each, ~448GB aggregate) for CPU/RAM-bound work, plus BC-250 GPUs
incoming for GPU-bound work. The live per-run hardware probe (`lumen_state.txt`)
only covers the Lumen head node itself, not the rest of the fleet, so this
fleet-wide invariant text is the only place the judge learns about other nodes'
capability — and it was still telling the judge to reject on capabilities the fleet
now has.

**Fix**: updated both blocks to describe current fleet hardware and require the
judge name which node a hardware-infeasible verdict was checked against, rather
than blanket-rejecting on stale single-node specs.

## Files

- `minerva_dedup.py` — patched `route_conflict()` (see diff vs. the live repo's HEAD).
- `minerva_proposal.py` — patched `INVARIANTS` / `LUMEN_STATE_FALLBACK` hardware text.
- `test_route_conflict.py` — regression test: `python3 test_route_conflict.py`.
- `install.sh` — change-safety replay (backup → apply → import-health → auto-revert).

## Deploy

Primary deploy is git: land this PR and let `lumen-pr` fast-forward
`/home/lumen/minerva` (same convention as wq#1997/wq#2005/wq#2010's
`minerva_verify.py` fixes). `install.sh` is the replay for a host where that
fast-forward hasn't happened yet.

## Re-opening proposals killed by these bugs

Both bugs produce a distinctive `decision_reason`:
- Bug 1: `decision_reason LIKE 'Duplicate: LiteLLM route %already exists%'`
- Bug 2: `decision_reason LIKE '%infeasible%' AND (decision_reason LIKE '%AVX%'
  OR decision_reason LIKE '%Broadwell%' OR decision_reason LIKE '%no GPU%' OR
  decision_reason LIKE '%CPU-only%')`

Re-review, not blind re-approval, is the correct remedy — resetting `decision` to
`pending` on matching rows in `/home/lumen/minerva/minerva.db` `proposals` table
routes them back through the (now-fixed) reviewer rather than auto-approving them.
This is a follow-up data migration against production, tracked separately from this
code fix so the fix itself can land and be verified independently.
