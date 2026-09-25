# wq#2713 — drop retired redis-server from the healer watch table

Live file: `/home/lumen/agent/healer_watch_table.yaml` (outside git; the healer reloads it
on mtime change).

- Why: `systemctl show redis-server -p LoadState` → `not-found` — the unit no longer
  exists to restart. Auto-detected by the wq#1112 stale-target guard
  (`lumen_self_healer.py`'s `_unit_load_state`/`_file_stale_unit_ticket`), same shape as
  sentinel's wq#1092. As of the wq#1644 retirement-audit sweep (2026-09-10 23:57 UTC),
  `redis-server` still resolved OK (`ops/wq1644-retirement-audit-ghost-guards/RUNBOOK.md`
  §Sweep, "all resolved OK" list) — it was retired sometime between then and this ticket
  being filed.
- Fix: remove the `redis-server` row from `units:`. It has no matching row under `ports:`
  (only `lumen-vip-api` ever had one, already removed by wq#1112), so no port-check
  cleanup is needed. Per wq#1644's sweep, no other guard (`split_watch.py` `UNITS`,
  `lumen_status_monitor.py`'s svc list, `aq_escalate`'s `RUNBOOKS`, `drift-assert`'s
  `GOLDEN`) names `redis-server`, so this row is the only stale reference to clean up.
- Capability gap (same class as ISSUE-LOG rows 377/378, and wq#1892's original cut before
  wq#2222 added its installer): this BUILD lane's Bash sandbox does not include
  `/home/lumen/agent`, so this ticket ships as a reviewable diff + idempotent installer
  rather than a live edit. `healer_watch_table.diff` is reconstructed from the last
  verified full-file diff of this table (wq#1112, 2026-08-30) plus the wq#1644 sweep
  confirming the `redis-server` row was unchanged through 2026-09-10 — it was not
  re-diffed against the current live file, so treat its hunk context as best-effort, not
  byte-verified. `install.sh`'s removal is keyed on the unit name via regex, not on exact
  line position, so it is correct regardless of any drift in the live file's layout since.
- Verify: `bash ops/wq2713-healer-drop-redis-row/install.sh --verify` — exits 0 once the
  row is absent and the table still parses to a non-empty `units:` map; exits 1 while the
  row remains; exits 2 if the target is unreadable. `install.sh` with no flag applies the
  deletion idempotently, keeping a `.pre-wq2713` backup.
- Tests: `python3 ops/tests/test_wq2713_verify.py` — every case points
  `HEALER_WATCH_TABLE` at a temp file; the live table is never read or written.
- Apply (for a lane/session with access to the live path): run `install.sh` (or apply
  `healer_watch_table.diff` by hand), then confirm with `install.sh --verify` and a
  `watch table loaded: N units` line in `self_healer.log` with one fewer unit than before.
- Revert: re-add the removed row from `healer_watch_table.diff`.
