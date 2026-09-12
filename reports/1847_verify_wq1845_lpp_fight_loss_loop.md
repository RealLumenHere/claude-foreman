# wq#1847 — Adversarial verify-pass of wq#1845 (LPP: FIGHT_LOSS_LOOP@65 pin never persisted)

**Verdict: CONFIRM** — the shipped diff (`461be08` `fix(stuck): the pin past K never
reached the file the agent reads`, merged to `main`, currently HEAD in `/home/lumen/pokemon`)
does what it claims. Read the PR cold, reconstructed both negative controls against the
pre-fix code independently (not taken on the author's word), and ran every regression
suite the PR cites.

## What was shipped

Two defects, one incident (FIGHT_LOSS_LOOP@65, frozen 246.3h, 2026-09-02 → 2026-09-12):

1. **The persist-gate bug.** `tools/lpp-stuck-supervisor` — the copy the systemd timer
   actually runs every 30 min — had its own inlined write to
   `runtime/supervisor_directive.json`, gated on `decision.get("intervened")`. wq#1091
   had already taught `lpp_stuck_intervene.main()` to persist a terminal directive past K
   (the "exhausted but still has a standing requirement" case), but that fix landed only
   in `main()`, never in the supervisor's own copy. The exhausted FIGHT_LOSS_LOOP@65
   directive was recomputed every tick for 246h and thrown away; the live pin on disk
   stayed `WARP_OSC@36` (Route 25/Bill's House, written 2026-09-12T10:55Z, a different
   incident) while the agent lost to Misty in Cerulean Gym.
   Fix: one shared writer, `lpp_stuck_intervene.persist_directive()`, called from both
   `main()` and the supervisor; the supervisor's predicate widened from
   `decision.get("intervened")` to `decision.get("directive")` (`tools/lpp-stuck-supervisor:63-69`,
   `tools/lpp_stuck_intervene.py:401-410`).
2. **The false-evidence bug.** `lpp_stuck_feedback.build_report()` printed the key's
   *entire* attempt history under a sentence dating the freeze to `since {lastAdvanceAt}`.
   FIGHT_LOSS_LOOP@65's three logged attempts were all made 2026-08-18, against a save a
   checkpoint restore later rolled back (eventFlags 43→39), 15 days before this freeze
   began — so the report claimed 3 interventions had been tried against a stall that had
   in fact received zero. That false evidence is what drove three redispatches to chase
   "grind harder" instead of the plumbing bug.
   Fix: attempts are now split on the freeze boundary the same sentence already names —
   only attempts with `ts >= lastAdvanceAt` count as "tried against this freeze"; older
   ones are counted (still spend K) but reported separately as
   `+N earlier attempt(s) ... still hold its K budget` (`tools/lpp_stuck_feedback.py:147-172`).

`tools/test_stuck1845.py` (new, 206 lines, sections A-I) covers both halves plus no-regression
checks (unspent keys still intervene/log, `--dry-run` stays read-only, an unstalled tick
leaves the directive alone, `report_signature` still masks only the clock).

## Method — reconstructed the negative controls, didn't take them on faith

The PR's notes assert "negative controls confirmed (B1 fails on old predicate, G2 on old
report)". Rather than trust that line, I rebuilt the pre-fix code path independently:

- Pulled `tools/lpp_stuck_intervene.py`, `tools/lpp_stuck_feedback.py`, and
  `tools/lpp-stuck-supervisor` at `461be08^` (`d338f83`, the commit immediately before
  the fix) via `git show`, copied them alongside the post-fix `lpp_stuck_mode.py` and the
  shipped `test_stuck1845.py` into an isolated scratch tree (outside the shared
  `/home/lumen/pokemon` checkout — `git status` there stayed clean throughout).
- Ran the shipped test suite against that pre-fix snapshot: it fails exactly where
  claimed —
  `AssertionError: B1: the tick must replace the stale pin, got WARP_OSC@36` — confirming
  the pre-fix supervisor really does drop the exhausted directive and leave the unrelated
  WARP_OSC pin in place.
- Isolated section G's assertion (`build_report` should not credit 2026-08-18 attempts to
  the current freeze) into a standalone repro against the pre-fix `lpp_stuck_feedback.py`:
  it also fails as claimed — the old `build_report` output includes
  `"attempt 1: grind_sub_objective (superseded)"` in the interventions line, i.e. exactly
  the false-evidence bug wq#1845 describes.
- Ran the shipped suite against the post-fix code: all 9 sections (A-I) pass.
- Ran the three regression suites cited in the PR notes against post-fix code:
  `test_stuck1826.py` (10 sections A-J), `test_stuck1118.py` (6 sections A-F),
  `test_stuck697.py` (all sections) — all pass, no regressions.
- Checked for any other live write path to `DIRECTIVE_PATH` outside the new
  `persist_directive()`: `grep -rn "DIRECTIVE_PATH"` across `tools/*.py` and
  `tools/lpp-stuck-supervisor` turns up exactly one writer plus its two call sites (the
  only other hit is a `.bak` file from 2026-08-28, unrelated dead code, not on any import
  path).
- Cross-checked ISSUE-LOG rows 352-354 (`/home/lumen/kb/ISSUE-LOG.md:405-407`) against the
  diff and commit — root cause, fix shape, and test citation all match what's actually in
  `461be08`.

## Judgment on scope

wq#1845's own notes flag a follow-up (wq#1846: the K budget is never episode-scoped, so
the "attempts predate a checkpoint restore" trap can still recur under the *obvious*
`lastAdvanceAt`-window framing — that's out of scope for this diff and correctly punted
rather than silently patched here). Not re-litigating that; it's the right call to leave
it as a separate ticket, not a reason to withhold CONFIRM on this one.

No breaking changes to `decide()`'s public contract (section A confirms wq#1091's
exhausted-directive shape is unchanged), no new external dependencies, `--dry-run`
still stays read-only (section E).

## Conclusion

Both defects wq#1845 reports are real, independently reproduced against the pre-fix
code (not just re-run of the author's own test file), and the fix addresses both without
regressing the three existing stuck-mode suites. CONFIRM.
