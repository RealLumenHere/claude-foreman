# wq#1739 — apply wq#1643 split-watch / healer-fallback diffs

Applies the two diffs prepared in `ops/wq1643-split-watch-retire-agent5/`
to the live files that lane could not reach (outside its `--add-dir`).

## Applied state (2026-09-20)

| live file | result |
|---|---|
| `/home/lumen/agents/split-watch/split_watch.py` | `split_watch.diff` applied. `.bak`: `split_watch.py.bak.20260920_pre_wq1739`. Retirement code at lines 38-49 (`RETIRED_DOC`, `_load_state`), 60-64 (`retired` short-circuit), 65-81 (all-units-not-found drain + persist + ntfy). |
| `/home/lumen/agent/lumen_self_healer.py` | `healer_fallback_table.diff` **not applied — superseded**. wq#1650 already pruned `FALLBACK_TABLE` (lines 49-62: no `lumen-prompt-engine`/`lumen-vip-api`/`lumen-vip-watcher`). Applying the stale diff would revert wq#1650's `ollama` rows. |

Both files pass `python3 -m py_compile`.

## Verification

- Queues were snapshotted before the drain to
  `/home/lumen/agents/split-watch/drain_snapshot_wq1739.json`.
- `journalctl -u lumen-split-watch`, first fire after the patch (20:52:34):
  `agent5 split (planner/generator/evaluator) confirmed RETIRED -- drained
  {'lumen5:plan_in': 350, 'lumen5:gen_in': 0, 'lumen5:eval_in': 0,
  'lumen5:dead': 187}`.
- Next fire (21:02:34): `agent5 split RETIRED 1789937554 — guard inert, see
  docs/fleet/retired_services.yaml` — no `ok depths=...` line.
- `state.json` holds `retired: true`; every later run returns before touching
  redis or systemctl.
- redis db5: `llen lumen5:plan_in` = 0, `llen lumen5:dead` = 0 (read via
  `python3 -c "import redis..."`; `redis-cli` is not on the worker allowlist).

## Left open: mask the timer

`lumen-split-watch.timer` is still `enabled`. It is inert (fires, prints the
RETIRED line, exits), but `systemctl disable`/`mask` is on the worker
deny-list. `disable-timer.sh` in this directory does it and refuses to run
unless `state.json` says `retired: true`. Run it as root from a lane that may
mutate units.
