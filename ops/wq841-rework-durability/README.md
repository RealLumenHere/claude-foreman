# wq#841 — durable rework signal

Full account: `reports/wq841_rework_rate_durability.md`.

| file | what it is |
|---|---|
| `wq_reopened_count.diff` | The patch applied live to `/home/lumen/bin/wq` (that repo is not commit-safe — see the report). Adds `tasks.reopened_count`/`reopened_at`, bumps them on every terminal → in-flight transition, prints `[REOPENED — reopened_count=N]`. |
| `backfill_reopened.py` | One-time audited seed of the column from the legacy `notes LIKE '%REOPENED%'` markers. `--audit` lists candidates; `--seed ID[:TS],... --apply` writes. Idempotent. |
| `ensure_wq_reopened.py` | Keeps that patch alive. `--check` says whether the live `wq` still bumps the column; `--apply` puts it back. Anchor-based, idempotent, backs up, verifies with `py_compile` plus a real add → done → reopen round-trip, rolls back if it broke anything. |

Applied on 2026-08-23:

```
backfill_reopened.py --seed 433:2026-08-22T04:36:00,755:2026-08-22T04:36:00,760:2026-08-22T04:36:00 --apply
```

## When the writer goes missing

`/home/lumen/bin/wq` is untracked and lanes rewrite it in place from older copies. That erased
this patch once already, on 2026-08-23 at 12:17Z, six hours after it landed — silently, because
the column survives its writer and the exporter kept reading it. `lumen_queue_reopened_writer_present`
is the alarm (`0` = the writer is gone, rework_rate is frozen rather than improving). The repair:

```
python3 ops/wq841-rework-durability/ensure_wq_reopened.py --check --json
python3 ops/wq841-rework-durability/ensure_wq_reopened.py --apply
python3 -m pytest harness/tests/test_wq_reopened.py harness/tests/test_wq841_writer_guard.py
```

`wq_reopened_count.diff` stays as the readable record of what the patch is. Do not feed it to
`patch(1)` any more — wq has moved on and the context no longer matches. `--apply` is the path.
