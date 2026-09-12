# wq#1871 — Adversarial verify-pass of wq#1867 (LPP: FIGHT_LOSS_LOOP@65 → OBJECTIVE_DRIFT)

**Verdict: CONTRADICT** — the merged fix (`tools/lpp_stuck_mode.py`, `tools/lpp_stuck_intervene.py`,
PR #130, commit `77d14c7`) correctly resolves the literal incident it was filed against (a won
Cascade Badge no longer re-diagnoses as `FIGHT_LOSS_LOOP@65`). But `_drift_diagnosis`'s fallback
anchor — matching a cleared gym into the current stall by TOWN NAME when no cleared gym's map id
is actually present in the warp window — is overbroad. It fires on a genuinely unresolved
readiness-gate stall at a **different, unbeaten gym in a different town**, as soon as any stale
warp from an earlier, already-cleared town's Pokecenter/City/Mart lingers in the same 64-warp
window. The resulting pin doesn't just misattribute the stall — it tells the agent a specific,
false thing: "the gym in `<the town it's actually stuck at>` is ALREADY BEATEN ... no gate refusing
you anything," while a real fight-readiness gate is in fact refusing that gym's entry. This
re-creates the exact "agent is fed a supervisor pin it trusts over its own reasoning" failure mode
wq#1867 was filed to kill, just inverted (false all-clear instead of false grind order).

## What the fix does correctly (confirmed)

- `cleared_gyms()`'s badge test (lower-badge-count win vs. current badges) is sound: verified
  `tools/test_stuck1867.py` 39/39 locally, and independently re-checked sections D1-D4 (junior
  trainer win doesn't clear, a loss never clears, a non-gym win never clears, a checkpoint
  rollback un-clears) by re-running the module's own functions against hand-built ledger records —
  all match the docstring's claims.
- The primary path (section A/E in the test file: cleared gym's own map id present in the
  window, or absent-because-refused-entry with only that gym's town in scope) correctly
  reclassifies to `OBJECTIVE_DRIFT` and anchors on the town, matching the live wq#1867 incident.
- `load_recent_turn_summaries()`'s flat-shape fix and the `NOISE_STORM` dead-read finding are
  real and independently verifiable against `src/events.ts`'s actual emit shape.
- The terminal-directive refactor (`_fmt_extras` shared between attempt and `EXHAUSTED` paths)
  is a real simplification and the KeyError-safety claim (`test I4`) checks out.

## Where it breaks: town-name matching conflates unrelated gyms

`_drift_diagnosis` (`tools/lpp_stuck_mode.py:446-473`):

```python
towns = {town_of(map_label(m, names)) for m in window}
hit = ([m for m in window if m in cleared] or
       [m for m in sorted(cleared) if town_of(cleared[m]) in towns])
```

The fallback branch only checks that *some* map in the window shares a first-name-token with a
*cleared* gym — it never checks that the window's actual stall evidence (the readiness-gate
shape `_readiness_stall` matched on) has anything to do with that gym. `towns` is built from
**every** map in the whole window, not just the maps that produced the readiness-stall shape.

Once a player holds 2+ badges, at least one earlier town's gym is always in `cleared`, and it is
entirely ordinary for a handful of stale warps from that earlier town (a Pokecenter heal, a Mart
stop, a return visit) to still sit inside a 64-entry window while the agent is now stuck at a
**different, unbeaten** gym elsewhere. `_readiness_stall`'s no-gym-warp branch (the one wq#1867
itself extended into `OBJECTIVE_DRIFT`) requires *zero* gym warps in scope precisely because a
readiness gate keeps the gym warp out of history — so there is no exact-map-id evidence to check
against, and the code falls straight to the town heuristic.

### Repro (ran against the actual module, not a hypothetical)

Cascade Badge (Cerulean Gym, map65) won at badges=1; player now holds badges=2 and is currently
stuck at the **Vermilion** Gym (map92, Lt. Surge / Thunder Badge — unbeaten), with the last 64
warps a Vermilion Pokecenter/City loop (`[5, 89]*30`) plus 4 stale Cerulean-City (map3) warps left
over from earlier travel:

```
cleared = {65: 'Cerulean Gym'}
window  = [5, 89]*30 + [3]*4          # zero gym warps in window — a real gate refusal
mode, ev, loc = classify_mode(window, [], NAMES, cleared=cleared, badges=2,
                               next_milestone='Vermilion Gym')
# mode == 'OBJECTIVE_DRIFT'   (should stay FIGHT_LOSS_LOOP / grind_sub_objective)
# loc  == 5                   ("Vermilion City")
# ev   == "the loop orbits Vermilion City around Cerulean Gym, whose badge is ALREADY WON
#          ... Nothing is refusing a gym fight here and there is no fight left to prepare
#          for: the stall is not leaving for the next milestone the frontier engine names
#          (Vermilion Gym)"
```

Rendered through `lpp_stuck_intervene.decide()`, the pin the agent actually reads:

> PIN: SUPERVISOR (1/3): **the gym in Vermilion City (map5) is ALREADY BEATEN** — its badge is
> on your trainer card and the fight ledger holds the win. **There is nothing left to prepare
> for here and no gate refusing you anything**, so stop cycling this town's doors ... Your next
> objective is Vermilion Gym. The ROUTE line in your prompt names that goal and the NEXT LEG
> that starts it — take that leg.

Badge count is 2 (Boulder + Cascade); the Thunder Badge (Vermilion, badge #3) has not been won.
The pin asserts the opposite of the true state and explicitly denies the gate that is the actual
blocker. This is not a labeling quibble — `FIGHT_LOSS_LOOP`'s `grind_sub_objective` prescription
(the one this pin displaces) is the only mode in this module that tells the agent to satisfy
`src/guidance.ts`'s gym-entry gate before re-attempting; `OBJECTIVE_DRIFT` explicitly orders it
to stop doing exactly that and walk back into the same wall.

`tools/test_stuck1867.py` cannot see this: every scenario in it (sections A, E) uses a single
cleared gym whose OWN town is the only town anywhere in the window. Section E deliberately
narrows to a 16-entry window that still contains only Cerulean-town maps. No test constructs a
window spanning two different towns' hub maps with the readiness-gate shape produced by the
town that is **not** the cleared one — the exact shape a mid-run agent with 2+ badges producing
warp history 64 entries deep across a town transition will legitimately produce.

## What's correctly fixed (not disputed)

- The literal wq#1867 incident (Cerulean Gym re-diagnosed after its own badge landed, same town,
  same window) is fixed and will not recur in that exact shape.
- `cleared_gyms()`'s badge-count semantics, the `NOISE_STORM` dead-read fix, and the shared
  `_fmt_extras()` refactor are all correct and independently re-verified.

## Recommendation

Anchor the fallback match to the readiness-stall's own evidence, not the whole window: only
credit a cleared gym via town-name if that gym's town is *also* the hub `_readiness_stall`
identified (e.g. require `town_of(hub_label) == town_of(cleared[gym_id])`, not merely "somewhere
in the window"), or drop the fallback entirely and require the exact map id (accepting that a
long-cleared gym whose last two warps have fully aged out of the window correctly stops being
diagnosable as drift — matching this module's own stated fail-closed doctrine for a rotated-out
ledger). Filing a judgment ticket (claude-opus-5@high) for the fix, since the correct anchor rule
is a design call, not a mechanical patch.
