# wq#1832 — Adversarial verify-pass of wq#1744 (LPP catch-fix + explore-override probe/design)

**Verdict: CONFIRM** — the shipped diff (PR #118, `2f3879e`, merged and still an ancestor
of `main` HEAD `8163667`) does what it claims. Two things could not be re-executed
in-session due to a known tooling gap (below); everything else was independently
re-derived from source and from fresh live data, not taken on the author's word.

## What was shipped

Test-only + docs, no production `src` changes beyond the probe itself:
- `src/probe_battlebag1663.ts` (+59/-16): `findFixture()` (picked the alphabetically
  first map35 fixture, no grass check) replaced with `fixtureCandidates()` (every
  qualifying fixture, grass-adjacent ones sorted first) plus a `MAX_FIXTURES=8` walk that
  tries candidates until one actually rolls a wild encounter. `forceEncounter()`'s
  direction list is now also sorted grass-first.
- `docs/EXPLORE_STALL_1744.md` (new, 135 lines): a design doc concluding the wq#1731 hard
  exploration override does not break the map35 (Route 24/25) x=4..5 stuck cycle, with a
  two-part fix proposal deferred to wq#1828 (no code landed here).

## Claim-by-claim check

1. **The probe bug was real** — pre-fix, `findFixture()` (old code, confirmed via
   `git show 2f3879e^:src/probe_battlebag1663.ts`) returns the first `readdirSync().sort()`
   match with no grass check. The fix's own comment names the specific offending fixture
   (`auto-mtxytpwj-map35`, (11,35), parked on the Nugget Bridge path) — Gen 1 only rolls
   encounters on a grass tile (`MapModel.isGrass`), so that fixture could never produce a
   battle and section C (the actual bag-open/select-past-page-1 assertions) never ran.
   That matches a real, previously-invisible coverage hole, not a manufactured excuse.

2. **The fix's logic is sound (static/adversarial read, since I could not execute it —
   see limitation below)**: `fixtureCandidates()` filters to map35, off-warp, out-of-battle,
   `partyCount>=1`, computes `onGrass` as current-tile-or-any-of-4-neighbors, sorts
   grass-first. `forceEncounter()`'s own direction candidates use the identical 4-neighbor
   set, so the two are consistent — a fixture flagged `onGrass` via its *current* tile
   (not a neighbor) still works, because pacing back onto that tile from a non-grass
   neighbor is itself a grass step. `idx` (the forced Poke Ball's bag index) is recomputed
   per-candidate inside the same loop iteration that calls `forceEncounter` on it, so it
   always corresponds to the fixture that actually won — no stale-index bug across the
   multi-fixture loop. 16 map35-named checkpoint files exist on disk
   (`checkpoints/*map35*`), giving `fixtureCandidates()` a real pool to search within the
   `MAX_FIXTURES=8` cap. No defect found.

3. **`items.ts` battle-bag fix (7950ea6) works live, independent of the probe** —
   current live run `events/live-agent-1789183317445.jsonl` (started 2026-09-12T03:21:55Z
   on a commit that includes the fix): 14 `use_item(poke_ball)` calls, 0 occurrences of
   "didn't open" (the pre-fix permanent-false-negative symptom). Outcomes are all
   `no poke_ball in bag` — confirmed at `src/items.ts:393`
   (`` `use_item: no ${ITEM_NAME[itemId]} in bag` ``) — i.e. the bag mechanism itself is
   working; the agent is simply out of balls.

4. **wq#1731's hard exploration override does NOT break the map35 cycle — re-derived
   fresh, not just re-read**:
   - `src/config.ts:154-155`: `windowTurns: envN("LPP_EXPLORE_WINDOW", 150)`,
     `minTiles: envN("LPP_EXPLORE_MIN_TILES", 5)` — matches the doc's cited defaults
     exactly.
   - `src/explore.ts:88-99` `assessStall()`: stall requires `d.complete && d.newTiles===0`
     AND (`seen.size < minTiles` OR `maps.length===1`) — matches the doc's whole-window-AND
     description line-for-line.
   - `runtime/stuck_escalation_overrides.jsonl` last write is `2026-09-11T01:19:17Z`
     (confirmed via `ls -la` mtime + tail). Current time at verify: `2026-09-12T10:31Z` —
     **~33h with zero fires**, longer silence than the doc's own 6.7h measurement.
   - `runtime/progress.json` (live, rolling 512-sample buffer) now spans turns 272-390
     (moved on from the doc's 220-338 window, as expected for a live file). Re-running the
     doc's own methodology on this fresh window: map35 x=4 (176) + x=5 (155) = 331/512 =
     **64.6%** of current samples are still in the exact strip the override exists to
     break — up from the doc's cited 60.9%, not down. The override is demonstrably still
     not resolving the cycle, on data the doc's author never saw.
   - Economics claim: live bag contents (`item234x1, dome_fossil, antidote, item49x1,
     item245x1, item80x1, repelx2, potionx2, item82x1`) match the doc's cited contents
     near-verbatim; catching is blocked by zero Poke Balls, not by either shipped fix.

5. **No code changes to the exploration override or its config landed in this diff** —
   confirmed via `git show 2f3879e --stat`: only `probe_battlebag1663.ts` and the new doc
   changed. The design in `EXPLORE_STALL_1744.md` (dominant-patch stall test, post-escape
   exclusion window) is explicitly deferred to wq#1828, which is the correct scoping —
   wq#1744 was asked to *verify*, and correctly stopped at a design doc instead of
   shipping an unreviewed behavioral change to production stall detection.

## Limitation (disclosed, not worked around)

Could not re-run `probe_battlebag1663.ts` itself (`npx tsx ...` was refused: "This
command requires approval"). Root cause: this verify-pass ticket's worktree is rendered
against the **foreman** repo's permission profile (no `node`/`npx`/`tsx` entries), while
the target repo (`pokemon`, reached only via `--add-dir`) has its own `node-ts` profile
that never applies here — the exact gap already logged as ISSUE-LOG **#149** (OPEN) from
an earlier verify-pass (wq#1370), and the same failure mode my own attempt-1 on this
ticket died to (classified `PERMISSION_LOOP`). Per doctrine (retrying a refused command is
not retryable), I did not retry it a second time; I verified statically instead — see
items 2 and 4 above for the independent, source- and data-level re-derivation that
substitutes for actually executing it. The author's cited result (7 PASS / 0 FAIL / 0
SKIP, "caught it with poke_ball! (2 left)" at bag index 15) is internally consistent with
`stackDeepBall`'s indexing (6 fillers + a ball landing past whatever the fixture's
existing bag held) and not contradicted by anything found here.

## Bottom line

CONFIRM. No regressions, no fabricated numbers, root-cause reasoning re-derived
independently and holding up (in fact strengthening) on fresher live data than the
original author had. Does not close wq#1828/#1829/#1830/#1831 (follow-ups, correctly
left open).
