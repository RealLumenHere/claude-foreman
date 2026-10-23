# wq#4578 (2/2) — carry the greedy value group into the renderer that is actually installed

Follow-up to PR #2416. Deploying that fix exposed that PR #2416 patched the wrong copy, and
caused a short live incident while finding out (recovered; `kb/ISSUE-LOG.md` row 1550).

## What

- `ops/wq4554-decisions-migration/context_builder.py:90-92` — same `(?P<value>.*\S|)` greedy,
  empty-safe value group as PR #2416 landed in the phase-1 bundle. This is the copy installed at
  `/home/lumen/minerva/memory/context_builder.py`.
- `ops/wq4554-decisions-migration/test_decisions_migration.py` — regression test, reusing the
  pinned fixture committed in the phase-1 bundle so both parsers are probed against identical
  bytes.
- `ops/wq4553-context-builder-live-roster/install.sh` — refuses (exit 2) when the live file is
  the phase-2 renderer; `--force` is the deliberate rollback.
- `ops/wq4554-decisions-migration/freeze_pg_memory_tables.sql` — `ALTER TABLE IF EXISTS`.
- `scripts/wq4578_outside_probe.py` — probes both bundle copies.
- `reports/wq4578_judgment_state_line_since_collision.md`, `pr_body_wq4578.md` — the ruling plus
  the PR #2416 body (its commit never got pushed before the squash merge).

## Why

The live renderer is the **wq#4554 phase-2** build, not wq#4553's: phase 2 moved decisions/facts
onto `minerva.db` and froze the four PG memory tables to `retired_*`. So:

- Fixing only the phase-1 bundle was a paper fix — the installed parser kept the non-greedy
  group. A probe over phase 1 alone passed while the live parser stayed broken, which is why the
  probe now covers both copies.
- Both bundles install to the same untracked path with no supersession check, so re-running the
  phase-1 installer rolled the live renderer back and it died on
  `psycopg2.errors.UndefinedTable: relation "fleet_decisions" does not exist`. Recovered from the
  backup `install.sh:33` takes (md5 `a1ebff40…` matches), live renderer is phase 2 again. The
  guard makes that un-repeatable without `--force`.
- Re-install is the only deploy path for a renderer that is untracked where it lives, so the
  phase-2 installer has to be re-runnable: its freeze step aborted under `set -e` the second time
  because `ALTER TABLE … RENAME` is not idempotent.

## How

Pattern-local in the renderer (no logic change), a `grep -q "wq#4554" "$LIVE"` guard in the
superseded installer, and `IF EXISTS` on four `ALTER TABLE` statements.

## Testing

- `python3 -m pytest -q ops/wq4553-context-builder-live-roster/test_live_roster.py ops/wq4554-decisions-migration/test_decisions_migration.py`
  → **12 passed** (9 + 3, including the new phase-2 regression).
- `python3 scripts/wq4578_outside_probe.py` → exit 0, both bundles, 3/3 rev-pinned prose values
  each (inputs from outside both bundles' fixture populations, per wq#2630).
- Falsifiable: with the non-greedy group restored, the probe exits 1 and the suites fail.
- Live: `lumen_state.py current` → 1244 lines, `grep -c "(since.*(since"` → 0, so the trap is
  latent and nothing rendered was corrupted.

## Risk / rollback

Low on the renderer (one regex group, no interface change). The installer guard can only refuse
work, never perform any. The SQL change makes an abort into a no-op. Rollback: revert this commit
and re-run `ops/wq4554-decisions-migration/install.sh` via `lumen-run`; the installer backs the
live file up before every install.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
