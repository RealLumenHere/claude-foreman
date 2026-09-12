# wq#1827 — Adversarial verify-pass of wq#1826 (WARP_OSC@36 far-side-ban fix)

**Verdict: CONFIRM** — the fix (`tools/lpp_stuck_intervene.py`, commit `3058a00`, PR #117,
already merged to `main`) holds under independent re-run, adversarial edge-case probing,
and a live dry-run against the actual production incident. No fabricated numbers, no
regressions found.

## What was shipped

`tools/lpp_stuck_intervene.py` (+110/-16), `docs/STUCK_INTERVENE.md` (+51),
`tools/test_stuck1826.py` (new, +155). Adds `_resolve_osc_ban()`: WARP_OSC's ban list is
now the FAR side of the classifier's own detected A↔B edge (via `M.detect_warp_osc`),
not `_resolve_loop_maps()`'s 64-warp-window top-2-by-frequency fallback that NAV_LOOP
still uses. Drops a heal/hub far side per the existing wq#1081/#1084 rule. Bumps
`WARP_OSC`'s prescription to `rev: 2` and threads `rev` through `open_attempt_count()` so
the K=3 budget the broken rev-1 ban already burned (3 supersedes, wrong maps) is refunded
for this mode only — every other mode's counters are untouched.

## Claim-by-claim check

1. **Live incident reproduces exactly as described** — `M.classify_mode(LIVE, ...)` on
   the actual `runtime/warp_history.json` tail (verbatim, not synthetic — confirmed the
   test's `LIVE` fixture matches the live file's last 16 entries byte-for-byte) gives
   `WARP_OSC@36`, edge `36<->88` (Route 25 ↔ Bills House). `I._resolve_loop_maps()` on
   the same window gives `[3, 36]` (Cerulean City + Route 25) — reproduces the shipped
   bad ban exactly, and confirms map 88 (the doorway actually being re-entered) was never
   in it.

2. **New resolver bans exactly the far endpoint, never the near side** —
   `_resolve_osc_ban(36)` → `([88], 36, 88)`. Checked the subset/no-self-ban invariants
   hold by construction (`here` can never appear in `ban`). Traced `M.detect_warp_osc`'s
   contract (`a,b` = current map, bounce partner; `a==b` on a genuine self-bounce) and
   confirmed `_resolve_osc_ban`'s `here == there` guard correctly no-ops that case rather
   than banning a phantom edge.

3. **Heal/hub far side is dropped, not banned** — bounce Route 24(35)↔Cerulean(3)
   standing on 35: ban is `[]`, matching the pre-existing wq#1081/#1084 rule
   (`M.is_heal_or_city`). Only `there` is checked against the hub list, which is correct:
   `here` can never be banned regardless, so checking it would be a no-op.

4. **Stale-window guard** — `_resolve_osc_ban(88)` against the same live window (location
   argument no longer matching the window's near side) returns `[]`. This is the same
   scope-mismatch class wq#1119 fixed on the classifier side; this diff closes the
   matching gap on the intervention side.

5. **End-to-end directive, live-shaped data** — `I.decide(state=stalled_state("WARP_OSC",
   36), log={})` on the live-shaped window: `gotoBan == [88]`, pin names the edge
   `"Route 25 (map36) <-> Bills House (map88)"` and the ban `"Bills House (map88) is
   BANNED"`, and contains neither `"Cerulean"` nor `"(map3)"`. NAV_LOOP's own
   `_resolve_loop_maps()` path (`gotoBan == [59,60,61]`) is byte-for-byte unchanged —
   confirmed by re-reading the diff: the new branch is gated on `mode == "WARP_OSC"`
   before falling through to the old `elif` for every other mode.

6. **K-refund via `rev`** — `open_attempt_count(log, key, rev=2)` on a log containing only
   `rev`-less (pre-fix) entries returns 0; three logged rev-1 WARP_OSC@36 supersedes do
   not count against the rev-2 budget. Confirmed a non-WARP_OSC mode's exhausted counter
   is untouched (its template has no `rev` override, defaults to 1, matches old entries).

7. **"One side, never both" seal-in claim checked against the actual enforcement code**
   (not just the docstring) — `src/agent.ts:262` loads `gotoBan` into a `Set`, `:802`
   refuses `enter_warp` by destination map, and `:296` clears the whole ban set only on
   `storyScore`/`badges`/`eventCount` advancing past the baseline. Since only the far side
   (88) is ever banned and the near side (36) stays open, and Bills House's only warp
   exits to Route 25 per game knowledge, the agent can always leave; banning both would
   have sealed it in with no advance-trigger available from inside.

8. **Full probe + regression suites, run directly, not trusted from the PR description**:
   - `python3 tools/test_stuck1826.py` → `10 sections A-J OK` (all pass).
   - `python3 tools/verify_671.py` → `22/22 passed, 1 skipped`; the skip
     (`live corpus stall no longer classifies FIGHT_LOSS_LOOP — got WARP_OSC`) is the
     suite's own documented "informational, never fails" corpus-drift skip and traces to
     `classify_mode`, which this diff never touches — not a regression from this change.
   - `python3 tools/verify_672.py` → `69/69 passed`.
   - `python3 tools/verify_673.py` → `39/39 passed`.
   - `python3 tools/test_stuck697.py` → `ALL PASS (8 sections, A-H)`.
   - `python3 tools/test_stuck1118.py` → `ALL PASS (6 sections, A-F)`.

9. **Live dry-run against the actual, still-open production incident** (not a fixture) —
   ran `I.decide()` directly (no `save_log`/directive write, so zero side effects;
   verified `runtime/intervention_log.json` and `runtime/supervisor_directive.json` mtimes
   unchanged after) against `M.compute()`'s real current state
   (`WARP_OSC@36`, `frozenHours≈243`): result is `attempt 1/3`, `exhausted: false`,
   `gotoBan: [88]`, pin names the Route25↔Bills House edge — the corrected remedy really
   does issue against the live stall, not just the fixture. `lpp-stuck-supervisor
   --dry-run --json` (which drives the same `decide()` call the installed timer will use
   on its next tick) agrees: `"intervened": true, "attempt": 1, "exhausted": false`.

## Not re-litigated

- `runtime/supervisor_directive.json` on disk is still the stale Aug-29
  `FIGHT_LOSS_LOOP@3` directive: `lpp-stuck-supervisor` only overwrites that file when
  `decision["intervened"]` is true, and every cycle since Aug 29 fell into an
  already-exhausted/terminal path for whatever was live at the time — a pre-existing
  design characteristic of `tools/lpp-stuck-supervisor`'s write-gating, not something
  wq#1826's diff touches or introduces. The installed timer (`lumen-lpp-stuck-supervisor.
  timer`) will pick up the fix and refresh the directive on its next tick.
- Per-position keying (`WARP_OSC@36` vs `WARP_OSC@64` vs `WARP_OSC@67` all seen as
  separate K=3 budgets in `runtime/intervention_log.json` for what may be overlapping
  bounce behavior) is `classify_mode`'s/`_key()`'s pre-existing location-keying design
  from wq#672/673, unchanged by this diff — out of scope for this verify-pass.

## Reproduce

```
python3 tools/test_stuck1826.py
python3 tools/verify_671.py
python3 tools/verify_672.py
python3 tools/verify_673.py
python3 tools/test_stuck697.py
python3 tools/test_stuck1118.py
python3 tools/lpp-stuck-supervisor --dry-run --json
```
