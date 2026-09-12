# wq#1873 — Adversarial verify-pass of wq#1872 (LPP: OBJECTIVE_DRIFT town-name fallback conflation)

**Verdict: CONTRADICT** — PR #131 (`bddcfa5`) correctly narrows the wq#1871-identified hole (a
cleared gym anywhere in the window matching by town) to a single "hub" town, and its own new test
section J (two towns in one window) passes, plus all 49/49 of `tools/test_stuck1867.py`. But the
hub is chosen by **majority vote over the window** (`Counter(cities).most_common(1)`), not by
*recency* or by the readiness-stall's own evidence. Whenever the window holds MORE stale warps
from an earlier, already-cleared town than warps from the town the run has just moved to and is
now stuck at, the fallback still anchors on the stale town and reports the SAME false
"ALREADY BEATEN" verdict wq#1872 was filed to kill — just gated on warp *counts* instead of warp
*presence*. This is not a knife's-edge tie: a plain majority for the old town reproduces it.

## What the fix does correctly (confirmed)

- Section J's own scenario (Cerulean cleared, Vermilion unbeaten, `[3,64]*4` + `[5,89]*12`-ish,
  i.e. the NEW town in heavy majority) reclassifies correctly to `FIGHT_LOSS_LOOP@Vermilion` — ran
  `python3 tools/test_stuck1867.py` against the PR head (`bddcfa5`, checked out via
  `git worktree add`): **49/49 passed**, including J0-J6.
- Sections A-I (the original wq#1867 fix: `cleared_gyms()` badge-count semantics, the primary
  exact-map-id path, the anchor-stability F1/F2 pair, the terminal-directive formatting) are
  unaffected and still correct.
- The narrowing itself (single hub instead of "any town in window") is real progress over
  wq#1867/PR#130 — it does fix the literal repro wq#1871 filed.

## Where it breaks: majority vote, not recency, decides the hub

`tools/lpp_stuck_mode.py:621-658` (`_drift_diagnosis`):

```python
cities = [m for m in window if map_label(m, names).lower().endswith(("city", "town"))]
hub = Counter(cities).most_common(1)[0][0] if cities else window[-1]
hub_town = town_of(map_label(hub, names))
hit = ([m for m in window if m in cleared] or
       [m for m in sorted(cleared) if hub_town and town_of(cleared[m]) == hub_town])
```

`Counter.most_common(1)` picks whichever town has the most city-labelled warps anywhere in the
64-entry window — it says nothing about which town the run is CURRENTLY at. `window[-1]` (the
actual most-recent warp — already computed and available) is never consulted for the tie/majority
case. A run that spent a long time in an earlier town before moving on, and has only recently
arrived at the next (unbeaten) gym's town, has by construction MORE stale warps for the old town
than fresh ones for the new — that is exactly what "just arrived and immediately stuck" looks
like in warp-count terms.

### Repro (ran against the PR head, not a hypothetical)

Cascade Badge (Cerulean Gym, map65) won at badges=1, player now holds badges=2, and is currently
stuck at the **unbeaten** Vermilion Gym (map92) with a real readiness gate refusing entry (zero
gym warps in scope — the same shape `_readiness_stall`'s own docstring names as its trigger). The
window holds more old-town than new-town city warps, which is the ordinary case for "just moved
on, still early at the new stop":

```python
cleared = {65: 'Cerulean Gym'}   # Cascade Badge, badges=2
window  = [3, 64, 3, 64, 3, 64, 3, 64, 3] + [5, 89, 5, 89, 5]   # 5x Cerulean City, 3x Vermilion City, 0 gym warps
mode, ev, loc = classify_mode(window, [], NAMES, cleared=cleared, badges=2,
                               next_milestone='Vermilion Gym')
# mode == 'OBJECTIVE_DRIFT'   (should be FIGHT_LOSS_LOOP / grind_sub_objective — Vermilion unbeaten)
# loc  == 3                   ("Cerulean City" — the STALE town, not where the run is stuck)
# ev   == "the loop orbits Cerulean City around Cerulean Gym, whose badge is ALREADY WON ...
#          Nothing is refusing a gym fight here and there is no fight left to prepare for:
#          the stall is not leaving for the next milestone the frontier engine names
#          (Vermilion Gym)"
```

Confirmed both a plain majority (5 vs 3) and an exact tie (5 vs 5, `Counter` ties resolve to
whichever key was inserted first — i.e. the chronologically OLDER town, since `window` is
oldest-first) reproduce the same false verdict; both cases ran directly against
`tools/lpp_stuck_mode.py` checked out at PR head `bddcfa5` (`git worktree add --detach` off the
fetched `refs/pull/131/head`, no re-implementation). The pin rendered through
`lpp_stuck_intervene.decide()` would tell the agent the Vermilion/Thunder badge fight is over and
order it toward `next_milestone`, while a live readiness gate is in fact refusing that gym's
entry — the identical failure mode wq#1867 and wq#1871 both named, just requiring "old town
outnumbers new town in the window" instead of "old town appears anywhere in the window."

`tools/test_stuck1867.py`'s new section J does not catch this: its `TWO_TOWN` fixture
(`tools/test_stuck1867.py:207-208`) gives the new town (Vermilion, 15 city-warps) a large majority
over the old (Cerulean, 5) — the ratio only ever tested in the direction that happens to work.

## What's correctly fixed (not disputed)

- The exact repro wq#1871 filed (new town in clear majority) is fixed.
- All of wq#1867's original scope (cleared_gyms badge-count semantics, primary exact-map-id path,
  anchor stability, terminal-directive formatting) remains correct.

## Recommendation

Anchor the fallback on the run's actual CURRENT town, not a majority vote over the whole window:
`town_of(map_label(window[-1], names))`, or narrow further to the most recent city/town-labelled
warp in the window, rather than the most frequent one. wq#1871's original recommendation — key the
match to the readiness-stall's own hub evidence rather than any window-wide aggregate — still
applies; "most frequent town in window" is a different window-wide aggregate with the same class
of failure. Filing a judgment ticket (claude-opus-5@high) since the correct anchor rule (recency
vs. frequency vs. re-deriving from `_readiness_stall`'s own hub) is a design call, not a mechanical
patch.
