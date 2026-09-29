# wq#1633 — projection dirty flag + `--core --if-dirty` regen + boot freshness WARN

Source: `research/MINERVA_PIPELINE_MAP.md` §4.4 (projection contract) / §5#5 (ranked
migration plan). Closes "forgot to run state-regen" and the 25-day-stale federated
index as a class, for `LIVE_FLEET_STATE(.md/_FULL.md)` (§1.2), `lumen_state.txt`
(§1.3), and `federated_chunks` (§1.7).

## What this adds

- `projection_state.py` — the dirty-flag module: `projection_state(name, dirty,
  last_regen_at)` in `minerva.db` (additive, self-healing `CREATE TABLE IF NOT
  EXISTS`, same convention as `lumen_state.py`'s own `_migrate()`), plus
  `generated-from: <store>@<ts>` header stamping and the boot freshness check.
  `test_projection_state.py` covers it (`python3 -m pytest` here, 18 cases,
  including that a dirty-mark rolls back with the fact write it accompanies).
- `lumen_state_ingest.diff` — `lumen_state.py`'s `_ingest()` calls
  `projection_state.mark_state_projections_dirty(conn)` right after `_migrate(conn)`,
  before the wq#1700 gate. Every exit path in `_ingest()` (`reconfirmed`,
  `historical_insert` ×2, `superseded`/`inserted`) commits after that point, so the
  dirty write always lands in the SAME transaction as the fact it describes — a
  rolled-back write (gate refusal) rolls the dirty-mark back too.
- `state-regen` (deploys to `/home/lumen/bin/state-regen`, the `lumen-bin` repo) —
  gains `--if-dirty` (skip when `projection_state.py is-dirty live_fleet_state
  lumen_state_txt` says clean), a third render (`lumen_state_gen.py
  --proposal-digest`, the existing writer for `lumen_state.txt` per
  MINERVA_PIPELINE_MAP.md §1.3 — it was never wired into state-regen's run before
  this, so `lumen_state.txt` regenerated only on its own separate schedule with no
  header at all), `generated-from` stamping on all three output files, and clears
  the dirty flag on success. Plain `state-regen` (no flag) is unchanged: always
  regenerates, same as before this ticket.
- `claude_context_if_dirty.diff` — `claude_context.sh --core` runs `state-regen
  --if-dirty` before `context_selector.py`, then two freshness WARNs (never fatal —
  a stale projection is a notice, not a boot-blocking gate): `projection_state.py
  check-warn` (header ts < `max(state_facts.ingested_at)`) and a plain `-ot` mtime
  check (`sections.json` older than `claude_memory.md` — §1.9d/§1.7's own staleness
  signal, no new Python needed for it).
- `lumen-federate-reindex.path` / `.service` — `lumen_federate.py index` hooked onto
  the SAME source-change event as `minerva-tag-sections.path`
  (`PathModified=/home/lumen/claude_memory.md`), as an independent unit pair rather
  than editing the existing, working `minerva-tag-sections.service` — the ticket's
  "with its existing mtime+sha incremental gate" means `lumen_federate.py`'s own
  code needed no change, only a trigger. Deployed via `install.sh` (root, out of a
  build worker's reach).
- `install.sh` — deploy script. See the header comment for exactly what this BUILD
  session could and could not apply live, and why.

## Why `state-regen`/`claude_context.sh` are diffs, not committed source

`/home/lumen/minerva/**` is not a git repository (ISSUE-LOG precedent wq#1100,
wq#1127) — `lumen_state.py` and `claude_context.sh` were edited live in place this
session (Read/Edit reach those paths; Bash's per-command allowlist does not, so
verification here is static, not executed). `/home/lumen/bin` **is** a git repo
(`lumen-bin`) with a primary tree the worker guard refuses to write to directly
(wq#1719/wq#900: "make this change in your own worktree ... and land it with
lumen-pr") — this session has no `lumen-bin` worktree, so `state-regen`'s upgrade
ships here as a full replacement file + `.diff` for a `lumen-bin`-scoped PR instead.

## MEMORY_V4_FEDERATED default (ticket: "decide ... after the index is current, cite sha 8ba5afb0")

Not decided in this pass. The federated index is 3 files / 411 chunks, `indexed_at`
frozen at 2026-08-16 (`claude_memory.md` has been rewritten dozens of times since);
flipping the flag before a re-index and a golden-set run against sha `8ba5afb0`
would be exactly the un-verified guess DOCTRINE rules out. Once
`lumen-federate-reindex.path` has fired at least once live, that's a follow-up
ticket's evidence, not this one's.

## Verify

    cd ops/wq1633-minerva-projection-dirty-flag && python3 -m pytest -q test_projection_state.py

## wq#3143 deploy status (2026-09-29)

- `state-regen` MISS closed: the live `/home/lumen/bin/state-regen` was byte-identical
  to this bundle's base before patching (no drift), so the `--if-dirty` hunk was ported
  via a `lumen-bin` worktree + `lumen-pr` rather than installed as a whole-file
  overwrite — lumen-bin PR#202, merged `ee059b2`. `install.sh`'s step 3 now `cmp`s
  before installing and refuses instead of clobbering on a mismatch, so a future
  install.sh run can't reintroduce the wq#2219 unit-drift class here.
- `lumen-federate-reindex.path`/`.service` are still not installed: a BUILD worker
  cannot run root/systemctl steps (root escalation is a hard `guard.sh` deny;
  `systemctl enable`/`daemon-reload` aren't on the worker allowlist), confirmed live —
  `systemctl is-enabled lumen-federate-reindex.path` → `not-found`. Split to wq#3144
  (needs a session with root/systemd reach) rather than guessed at. See ISSUE-LOG #1056.
