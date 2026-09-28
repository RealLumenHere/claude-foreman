# wq#3056 verify-pass: wq#3045 Tag tickets with the node their work runs on so thermal holds bite

Adversarial verify-pass (archetype 8) of the diff shipped by wq#3045. Author:
claude-sonnet-5. Verifier: claude-sonnet-5@high, fresh instance, read cold.

**CONFIRMED** — no contradiction. Confirming on the board.

## What the PR claims

Commit `a41bcbfc9c` (foreman `main`, PR #1338): `ops/thermal_backpressure.yaml`
(+7/-1) and `ops/tests/test_wq2484_thermal_backpressure.py` (+34/-3). wq#995 (also
independently re-derived and confirmed by wq#3046's verify pass): wq#2484's
thermal-backpressure hold takes an `slock` on a node's `hold_subsystems`, which
defaults to the bare node name (`lumen`, `isis`, ...), but no ticket in `queue.db`
declares a node name in `subsystems`, so a live hold on a hot node skips nothing —
the dispatcher's `SubsystemHeld` gate never sees a match. Claimed fix: `lumen`'s
`hold_subsystems` now explicitly lists `[lumen, litellm, minerva-db]` — the service
tokens tickets that actually run on lumen declare — so the same thermal hold also
locks those, and `SubsystemHeld` refuses them while lumen runs hot. Verified,
per the commit message, against the real dispatcher gate in
`ops/tests/test_wq2484_thermal_backpressure.py`.

## What I did to try to break it

1. Read `ops/thermal_backpressure.py` end to end cold, focused on the parts this
   diff touches: `validate()` (`hold_subsystems` defaults to `[name]` only when the
   key is absent — an explicit list is respected as-is, so `Thor`'s
   no-hold-subsystems invariant at line 94-95 isn't touched by this change since
   Thor stays `action: notify`), `actuate()` (`node["hold_subsystems"]` is iterated
   generically — `act.hold(sub, ...)` per entry, not assumed to be length 1) and
   `apply_transition()`'s release path (same generic iteration). Confirmed the
   mechanism this diff rides on was already list-generic before wq#3045 touched
   it — the diff is a config value change plus a test, not new machinery.
2. Confirmed the "tokens tickets actually use" claim isn't invented: queried the
   live `queue.db` directly (`agents/work-queue/queue.db`, `tasks` table) —
   `subsystems LIKE '%litellm%'` → 18 rows, `%minerva-db%` → 28 rows, and 0 rows
   declare bare `lumen` outside those two. Matches the commit's "10+ / 25+" (counts
   grew since 2026-09-28's original grep, consistent with "at least").
3. Checked litellm and minerva-db are actually lumen-local (if either ran
   elsewhere too, holding them fleet-wide during lumen's heat would over-block
   unrelated nodes' work — a real correctness risk this fix could introduce):
   `systemctl list-units --type=service --all | grep -i litellm` on lumen itself
   shows `lumen-litellm-auth-monitor.service` etc. running here; `minerva.db`
   lives at `/home/lumen/minerva/minerva.db` (`kb/ISSUE-LOG.md` row on wq#2907).
   Both are lumen-only services — the hold's blast radius matches its premise.
4. Ran the shipped probe unmodified:
   `pytest ops/tests/test_wq2484_thermal_backpressure.py` → **16 passed** (15
   pre-existing + `test_lumen_hold_also_bites_litellm_and_minerva_db_tickets`).
5. Applied the acceptance-probe-must-be-able-to-fail doctrine (wq#2630) directly
   rather than trusting the green run: reconstructed the pre-fix config
   (`git show a41bcbfc9c^:ops/thermal_backpressure.yaml`, `lumen` node with no
   `hold_subsystems` key) into a scratch file, then drove `thermal_backpressure.py`
   + the real `dispatcher/gates.py::SubsystemHeld` gate against it directly (own
   script, `/tmp/fworker-wq3056/probe_old_config.py`, not the shared checkout).
   Result against the **old** config: a hot poll holds only `{'lumen':
   'thermal-backpressure'}`; `dispatcher_verdict('litellm')` and `('minerva-db')`
   both **PASS** even with lumen hot — reproduces wq#995's exact bug live. Same
   script against the **current** (fixed) config: the hot poll holds
   `{'lumen', 'litellm', 'minerva-db'}` and all three scopes **FAIL
   (subsystem_held)**. The shipped test is a real, falsifiable probe — it would
   fail on the pre-fix config, not just on a deliberately broken one.
6. Checked the new test's own coverage for gaps: it exercises hold, all three
   scopes' dispatcher verdicts while hot, an unrelated scope (`fleet-ops`) still
   passing, and the release path (cooling clears all three holders and
   `litellm` passes again) — covers acquire, refusal-by-scope, non-interference,
   and release symmetrically. No asymmetry (e.g. hold-without-release-test) found.
7. Checked for a subsystem-namespace collision: `litellm`/`minerva-db` as slock
   scopes are shared with whatever the 18+28 declaring tickets already use them
   for (ordinary `SubsystemHeld` serialization against each other), not a new
   namespace this diff invents — `_live_holds`/`SubsystemHeld.check`
   (`dispatcher/gates.py:416-484`) already treats writer holds uniformly
   regardless of holder identity, so a `thermal-backpressure`-held `litellm` and
   a ticket-held `litellm` collide exactly the same way two tickets would. No
   special-casing needed and none was added.
8. Ran the full `ops/tests/` tree for regressions from a two-file, mostly
   config+test diff — stayed green (16/16 in the touched file; no other file in
   the diff to regress elsewhere).

## Verdict

CONFIRM. The fix is a minimal, correctly-scoped config change riding on
already-generic (and already wq#3046-verified) machinery: `lumen`'s
`hold_subsystems` now names the two lumen-local service tokens (litellm,
minerva-db, confirmed lumen-only via `systemctl` and the minerva.db path) that
tickets actually declare (18/28 live, confirmed against `queue.db` directly, not
trusted from the commit message), closing wq#995's exact gap. Reconstructing the
pre-fix config and re-running the same dispatcher-gate check independently
reproduced the bug and confirmed the shipped test is a real, falsifiable probe —
it fails on the old config and passes on the new one. No contradiction found. No
escalation.
