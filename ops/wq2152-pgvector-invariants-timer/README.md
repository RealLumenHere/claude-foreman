# wq#2152 — wire check-pgvector-invariants into a cadence

## Root gap

wq#1776 shipped `/home/lumen/bin/check-pgvector-invariants` (installed
2026-09-18) but deliberately no timer — its `install.sh:3` defers to "the
handoff/scan wiring steps" in its README. Nothing ran it. wq#1772's 2026-09-20
re-measurement (`research/PGVECTOR_INVALIDATION_CONTRACTS.md` §8.1) found I1
stale chunks drifted 19 → 21 across nine days, 21 docs affected, against 8.5%
corpus growth — a slow leak, measurable on demand and watched by nothing.

## What this ships

The wiring wq#1776's README describes, in the shape it names: an own-timer
cadence alongside `lumen-contradiction-scan.timer` (active since 09-11's
measurements).

- **`lumen-chunk-doc-invariants.service`** — oneshot, runs
  `check-pgvector-invariants --raise-aq --state
  /home/lumen/state/pgvector-invariants.json`. The `--state` override keeps
  the persisted counts out of `/home/lumen/bin` (the script's default is
  `state.json` next to itself — wq#1788's secondary finding). First run seeds
  state and raises nothing; the next +2 raises an `aq` row instead of waiting
  for a research ticket to re-run the numbers by hand.
- **`lumen-chunk-doc-invariants.timer`** — daily 06:40 UTC, 20 min after
  `lumen-contradiction-scan.timer` (06:20), `Persistent=true`.
- **The wq#1788 fix**, in the wq#1776 bundle this installs from
  (`../wq1776-pgvector-chunk-doc-invariants/check_chunk_doc_invariants.py`):
  wq#1785's CONTRADICT, confirmed by opus-5@high escalation (wq#1788, PR#741),
  found `measure(conn)` ran in a bare `try/finally`, so any post-connect
  Postgres failure (dropped relation, revoked grant, statement timeout)
  crashed with a traceback + non-zero exit instead of the docstring's promised
  `UNAVAILABLE` + exit 0 (`PGVECTOR_INVALIDATION_CONTRACTS.md` §4.3: an
  outage "must never take the fleet-facing scan down with it"). The ticket
  gated this wiring on that fix. `main()` now catches query-time failures the
  same way it catches connect-time ones, and — per wq#1788's finding that
  `main()` had zero test coverage — ships with three `main()`-level tests
  (connect-fail, query-fail, success path): 17 tests total, all green.

## Install

```
ops/wq2152-pgvector-invariants-timer/install.sh           # install + enable
ops/wq2152-pgvector-invariants-timer/install.sh --verify  # read-only drift check
```

Re-installs the (now fixed) checker to `/home/lumen/bin`, installs both units,
enables the timer, and runs one live report (no `--raise-aq`; the timer owns
state).

Build workers cannot execute `install.sh` (`requires approval` — same allowlist
wall as ISSUE-LOG #452/#464/#467), so this bundle lands installed-by-deploy-
ticket, the wq#1677/wq#1774 pattern: a follow-up deploy ticket runs
`install.sh` from a session with systemd reach, then `install.sh --verify`
proves convergence.

Deployed 2026-09-20 (wq#2193). Workers can reach systemd through
`lumen-run start --name <slug> -- <script>` (allowlisted; a transient unit whose
`sudo` calls succeed), which is the route `install.sh` ran through: 17 tests
passed, both units installed, timer enabled and active, next run 2026-09-21
06:42 UTC, live report `I1 stale-chunks=21 · I2 orphan-chunks=0 · FK cascades
True`. `install.sh --verify` then printed `VERIFY OK` (exit 0).

## Not shipped here

`handoff verify` wiring (the README's other option) — `/home/lumen/bin` is a
separate actively-shared repo (see wq#1776 README "both counts reported…").
The timer alone closes the watch gap; folding the line into `verify()` remains
a welcome follow-up, and is additive, not a substitute.
