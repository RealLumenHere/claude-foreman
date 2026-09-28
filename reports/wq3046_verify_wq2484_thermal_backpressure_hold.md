# wq#3046 verify-pass: wq#2484 Thermal backpressure (hold a hot node's subsystem)

Adversarial verify-pass (archetype 8) of the diff shipped by wq#2484. Author:
claude-opus-5-5. Verifier: claude-sonnet-5@high, fresh instance, read cold.

**CONFIRMED** — no contradiction. Confirming on the board.

## What the PR claims

Merge commit `05270595e4` (foreman `main`): `ops/thermal_backpressure.py` (+`.yaml`),
`ops/tests/test_wq2484_thermal_backpressure.py` (+2 CSV fixtures), and
`ops/wq2484-thermal-backpressure/{install.sh,*.service,*.timer}`. 7 files, 2010
insertions, **zero deletions and zero existing files touched** — the diff is purely
additive. Claimed: a guard that reads CPU package temp from existing netdata
(+ wq#2510's fan-floor verdict), holds the node's subsystem via `slock` with
raise/clear hysteresis (per-node thresholds from observed history), one `aq` row per
hold/release transition, tested 15/15 against real `slock` and the dispatcher's real
`SubsystemHeld` gate.

## What I did to try to break it

1. Read `ops/thermal_backpressure.py` end to end cold: config validation, the pure
   `evaluate`/`next_temp`/`next_fan` state machine, the `decide`/`actuate` transaction
   split, `Actuator` (slock+aq wrapper), and the CLI.
2. Ran the shipped probe unmodified: `pytest ops/tests/test_wq2484_thermal_backpressure.py`
   → **15 passed**. Ran the full `ops/tests/` tree for regressions from a purely-additive
   diff → **195 passed**, nothing broken elsewhere.
3. Independently recomputed the fixture's claimed stats instead of trusting the
   docstring: `wq2484_lumen_package_0925.csv` is exactly 1200 rows (60 s over 20h,
   2026-09-25T00:00→20:00Z) with `max=77.3`, `min=51.5` — matches
   `test_hottest_real_day_does_not_hold_at_the_shipped_thresholds`'s asserted `77.3`
   exactly, so the "hottest real day still doesn't hold" claim isn't a fixture that was
   quietly built to pass.
4. Checked the acceptance-probe-must-be-able-to-fail doctrine (wq#2630) directly, since
   a same-fixture/same-threshold test can vacuously pass. The suite already carries its
   own negative control (`test_same_day_holds_once_the_line_is_below_it`: pull `raise_c`
   under the day's peak and it fires) — good design — but I didn't stop there and ran two
   of my own mutations against the real logic, in my own worktree copy only (not the
   shared `/home/lumen/foreman` checkout), reverting after each:
   - Removed the exactly-once `aq` guard (`if tr["aq_at"] is None:` → `if True:`):
     `test_worker_lock_defers_the_hold_and_the_next_poll_takes_it` failed
     (`2 != 1` aq calls), 14/15 passed. The "at most one aq row per transition" claim is
     load-bearing and the suite catches its removal.
   - Removed hysteresis (`next_temp` → always `mean > raise_c`, no `hot`-dependent
     branch): `test_oscillation_across_the_raise_line_does_not_flap` failed (`7 != 1` aq
     calls) and `test_hot_holds_once_cool_releases_and_nothing_persists` failed, 13/15
     passed. The core "a reading sitting on the line cannot flap" claim is real, not
     just asserted.
   - Reverted both; diffed my worktree copy against `/home/lumen/foreman`'s to confirm
     byte-identical after revert; re-ran the suite clean (15/15).
5. Traced the dispatcher integration isn't faked: `EndToEnd` tests point `WQ_DB` at a
   throwaway sqlite db, call the real `/home/lumen/bin/slock` binary via `Actuator`, and
   call `dispatcher/gates.py`'s real `SubsystemHeld().check()` — not a stub. Confirmed
   `_live_holds()`/`_split_sub()`/`_parse_subs()` (`dispatcher/gates.py:402-436`) key on
   the same lowercase node-name tokens `thermal_backpressure.yaml`'s
   `hold_subsystems` defaults to, so a hold on `isis` and a ticket declaring
   `subsystems: isis` really would collide through the gate as shipped.
6. Checked `slock`'s actual output text against what `Actuator.hold`/`.release` treat as
   success (`bin/slock:226-242`): a release on nothing held returns rc 0 (not gated on
   the `"held by"` substring check at all, so my initial suspicion of a false-negative
   release-of-nothing path was wrong), and a release refused because another holder owns
   it returns rc 1 with `"held by"` in the text, which `Actuator.release` explicitly
   treats as success (their lease being gone is the actual postcondition wq#2484 wants).
   Also checked `waiting_higher_priority` (`bin/slock:129-148`) against a `holder` string
   that isn't a ticket claim (`thermal-backpressure`): `my_pri is None` short-circuits to
   `[]`, so the priority-inversion warning path can't crash on a non-ticket holder.
7. Checked Thor's protection (instinct 2) survives config tampering:
   `test_thor_can_never_hold` covers direct `action: hold`, a smuggled
   `hold_subsystems` key, and a same-IP alias node — all three raise `ConfigError` via
   `validate()`. Confirmed by reading `validate()` itself (`thermal_backpressure.py:76-106`),
   not just trusting the test names.
8. Looked for the failure mode a fixed CSV window can't exercise: `resolve_chart`/
   `read_mean`/`read_temps` are the actual live-netdata HTTP path (curl + JSON + chart
   discovery by prefix/suffix), and **no test exercises them** — every `EndToEnd` test
   injects a `temps` dict directly, and every `Rule` test calls `evaluate()` over
   CSV-replayed samples, bypassing `read_temps` entirely. This is a real coverage gap.
   It is not a correctness defect on its own: `read_temps` wraps the fetch in
   `try/except` and any failure (0 or 2+ chart matches, an unreachable host, bad JSON)
   degrades to `(None, reason)`, which `next_temp`/`next_fan` already treat as "no
   reading, state carries, never reads as cool" — the fail-safe direction. Noting it
   rather than treating it as a break: nothing in the shipped code contradicts the
   ticket's ask because of this gap, but a future change to `resolve_chart` (e.g. a
   second matching chart appearing after a netdata upgrade) would have no test to catch
   a regression there.

## The self-disclosed limitation (checked, not rediscovered)

wq#2484's own done-notes and `kb/ISSUE-LOG.md` #995 already state: no ticket in
`queue.db` today declares a node-name subsystem (`lumen`/`isis`/`hathor`/`nephthys`/
`khepri`), so a live hold currently gates nothing in practice, and the worker had no
root to install the timer. Both are filed as follow-ups (wq#3044 install, wq#3045 node
tagging), status `PARTIAL` by the author's own accounting, not `done`-and-silent. I
verified this is accurately described rather than papered over: `SubsystemHeld.check()`
(`dispatcher/gates.py:454-457`) returns `PASS` immediately whenever a ticket's
`subsystems` field is empty, and nothing in this diff populates that field on any
ticket. wq#2484's actual ask — "hold the affected subsystem... the dispatcher already
skips tickets whose subsystem is held" — is met exactly as scoped: the mechanism is
real and dispatcher-integrated (§5 above), the fleet-wide gap in ticket tagging is a
separate, already-tracked problem, not a defect in this PR.

## Verdict

CONFIRM. The state machine, hysteresis, exactly-once `aq` discipline, and dispatcher
hold/skip integration all hold under mutation and under a fresh adversarial read — two
targeted mutations of load-bearing logic were both caught by the shipped suite, so the
15/15 pass is not tautological. The diff is purely additive (no existing file touched),
and the full `ops/tests/` tree (195) stays green. One real gap noted for the record
(no test coverage on the live-netdata HTTP path) but it degrades fail-safe, not
fail-open, so it does not rise to a contradiction. No escalation.
