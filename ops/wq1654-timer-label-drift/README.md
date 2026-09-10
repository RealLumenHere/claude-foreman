# wq#1654 — timers labelled DISABLED/STAGED/Frank-gated are live and running

Source: `research/FLEET_SERVICE_ATLAS.md` §7 F-3 (wq#1627). Nine fleet timers
carry a `Description=` claiming they are off pending Frank's enable, but
`systemctl show` confirms all nine are `ActiveState=active` +
`UnitFileState=enabled` right now (captured 2026-09-10 07:5x UTC — see
`test_label_drift_check.py` fixtures for the exact live strings). Frank was
told these were off; this is a REPEAT-class label lie.

## Decision (2026-09-10, full autonomy per instinct #2 — no Frank-gate)

**Keep all nine, fix the label on all nine.** None are destructive: six are
read/ingest/audit/bench jobs with no side effects beyond their own report or
data row, and the other three (`lumen-foreman-dispatch/health/selfheal`) ARE
the live wq dispatch broker — disabling them stops the pipeline outright.
Disabling any of the nine would either lose real work already validated as
safe, or break production. Full rationale and the corrected `Description=`
text per unit: `ingest_decisions.py:DECISIONS`.

The decision is recorded as a `timer_disposition` fact per unit via
`ingest_decisions.py`, using the gated wq#1632 `state_lifecycle.ingest()`
path (`state_lifecycle.py:142`) into minerva `state_facts` (lumen_state).

**Scope note:** actually rewriting `Description=` on the live units requires
the git-tracked `/home/lumen/fleet-units` baseline (`unit-drift`'s `REPO`,
`/home/lumen/bin/unit-drift:21`) so the fix doesn't itself register as drift
against that baseline. That path is outside this worker's sandbox (not this
worktree, not an `--add-dir`) — filed as a follow-up ticket for a worker
scoped to `fleet-units`, not parked as a Frank-gate (no human action needed,
just different filesystem scope).

## Files

- `label_drift_check.py` — the gate: `check` scans every live timer via
  `systemctl show` and fails (exit 1) on any unit that is enabled+active
  with a Description matching `DISABLED|STAGED|Frank-gated`. Confirmed
  against live state: catches all nine named units plus one more
  (`power-remediate.timer`, description contains "STAGED" while active —
  out of scope for this ticket's decision set, noted for a future audit).
- `ingest_decisions.py` — the nine keep/fix-label decisions + the ingest
  driver.
- `test_label_drift_check.py`, `test_ingest_decisions.py` — `python3 -m
  pytest` here.

## Running

```
python3 label_drift_check.py check        # live systemctl, exit 1 on drift
MINERVA_DB=/home/lumen/minerva/minerva.db \
  python3 ingest_decisions.py             # write the nine decisions
```

## Wiring into the fleet gate

`/home/lumen/bin/unit-drift`'s `cmd_check()` (`/home/lumen/bin/unit-drift:141`)
only diffs unit file bytes and enable-symlinks against the git baseline — it
never inspects `Description=`/`ActiveState` semantics, so it cannot see this
class of drift today. `label_drift_check.violations()` is the missing check;
wiring it into `cmd_check()` (or the enable-state-only `lumen-cron-deadman`
sweep) is a `/home/lumen/bin`-repo change and outside this branch's diff —
tracked as a follow-up so the real production tool gets the gate, not just
this worktree's copy.
