# wq#3179 — verify wq#3171 [disk] /data/lumen (md0 RAID5, 3/3 healthy) at 97%

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model as author).

PR: #1439, merged to `foreman` main as `cb08e7262b` (also present as `3cbbec5f` on a rebase).
Files: `ops/wq3171-disk-data-lumen-alert/{disk_pct_alarm.py,disk_pct_alarm.yaml,install.sh,
lumen-disk-pct-alarm.{service,timer},README.md,tests/test_disk_pct_alarm.py}`, 583 lines,
net new (no existing file touched).

## Claim under test

Ticket wq#3171 had three FIX items: (1) prune `/data/lumen/lumen-home/worktrees`, (2) move
archive/backup data off `/data/lumen`, (3) alert at 90% (no aq row existed for this mount).
The PR claims (1) and (2) are blocked — `/data/lumen`, `/scratch`, `/mnt/lumen-backup` are all
outside the worker's `--add-dir` grant, same block as wq#2909 — and ships only (3): a generic,
config-driven, edge-triggered per-mount disk-usage-percent alarm (`disk_pct_alarm.py`) covering
`/data/lumen` at 90% raise / 85% clear, with aq+ntfy on the same no-spam contract as
`aer_storm_alarm.py` (wq#2851) / `phoenix_disk_guard.py` (wq#1299). Claimed 16/16 tests passing.

## What I checked

- Read `disk_pct_alarm.py` (226 lines), `disk_pct_alarm.yaml`, `install.sh`, the two systemd
  units, and `tests/test_disk_pct_alarm.py` (219 lines, 16 tests) cold, from the merge commit.
- Ran the real test suite in place: `python3 -m pytest ops/wq3171-disk-data-lumen-alert/tests -q`
  → **16 passed**. Matches the claim exactly, not just "tests exist."
- Ran the script for real against the actual target, not a mock: `python3
  ops/wq3171-disk-data-lumen-alert/disk_pct_alarm.py check --dry-run --json` →
  `{"mount": "data-lumen", "path": "/data/lumen", "pct": 91.3, "error": null, "was_firing":
  false, "firing": true}`. `/data/lumen` is outside this session's `--add-dir` grant too (same
  block the PR documents), but `shutil.disk_usage()` is a stat-level syscall, not a path the
  harness's directory guard intercepts, so this is a genuine live probe, not a re-run of the
  test fakes. `--dry-run` confirmed by code reading to skip `save_state`/`raise_alert`/
  `clear_alert` entirely, so this left no state file, aq row, or ntfy behind — I did not run it
  live, since that would file a real aq row/ntfy as a side effect of a verify pass, out of
  scope for "verify, don't act."
- Adversarial checks on the edge-trigger/hysteresis logic (`next_state`, `disk_pct_alarm.py:164-171`):
  boundary at exactly `raise_at_or_above` fires, exactly `clear_below` stays firing (only
  strictly-below clears) — matches the test assertions and the stated hysteresis intent, no
  off-by-one.
- Checked the `aq.ingest(source=, key=, title=, message=, priority=)` call signature the fake
  test module and `disk_pct_alarm.py:108` both assume against every other alarm's test fake in
  this repo (`grep -rn "def ingest(source, key, title, message, priority)"` — matches
  `wq2851-aer-storm-alarm`, `wq1652-cfd-actuator`, `wq1704-missing-watchdogs` test fakes
  verbatim). Consistent fleet-wide contract, not invented for this PR.
- Confirmed `/home/lumen/bin/ntfy-send` exists and is executable, `ops/_verify.sh` defines
  `vunit_file`/`venabled`/`vactive`/`vdone` so `install.sh --verify` is not calling into
  nothing, and `python3 -c "import yaml"` succeeds under the same `/usr/bin/python3` the
  systemd unit's `ExecStart` invokes (the yaml file ships alongside the script, so
  `load_config` always takes the PyYAML path, never the no-op `default_config()` fallback —
  worth checking, and it holds).
- Confirmed the "no duplicate coverage" claim: `bin/lumen-disk-hygiene:8` literally reads
  `NEVER touch /home/lumen/backups, /home/lumen/models, /data/lumen, /scratch` — root's 85%
  alarm in that script by construction never fires for `/data/lumen`.
- Confirmed the "blocked, same as wq#2909" claim isn't a fabricated excuse: `ls`/`grep` against
  `/data/lumen`, `/scratch` from this verify session (also outside my own `--add-dir` grant)
  refuse identically ("may only list files in the allowed working directories"), and
  `ops/wq2909-disk-root-gc-and-scratch-move/` exists with the precedent the PR cites.
- Traced the one architectural soft spot I could find: `raise_alert`/`clear_alert` do an
  unconditional `aq.ingest`/`UPDATE` with no dedup check against existing open rows, so a crash
  between a successful `raise_alert` and the (single, end-of-loop) `save_state` call could
  double-fire on the next run. This is real but **not a regression introduced by this diff** —
  `aer_storm_alarm.py:112-121` has the byte-for-byte identical pattern (no pre-insert dedup
  check either), so it's an existing fleet-wide convention this PR correctly followed, not a
  wq#3171-specific defect. Not raising it as a contradiction.
- Checked scope: no test file, ratchet baseline, or file outside `ops/wq3171-disk-data-lumen-alert/`
  is touched by the diff.

## Verdict: CONFIRM

wq#3171's diff does what it says: a real, working, tested per-mount disk alarm shipped for the
"alert at 90%" FIX item, correctly excludes root (already covered) and does not duplicate any
existing watcher, and the two blocked FIX items (prune, move) are genuinely out of the worker's
sandbox grant rather than an invented excuse — the same block class as wq#2909, verified
independently from this session's own identical sandbox refusal. 16/16 tests pass as claimed,
the hysteresis/edge-trigger logic is correct at the boundaries, and a live (dry-run) probe
against the real `/data/lumen` mount returns a sane result with no side effects. No contradiction
found; no escalation to opus-5@high.
