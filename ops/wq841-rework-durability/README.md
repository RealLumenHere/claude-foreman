# wq#841 — durable rework signal

Full account: `reports/wq841_rework_rate_durability.md`.

| file | what it is |
|---|---|
| `wq_reopened_count.diff` | The patch applied live to `/home/lumen/bin/wq` (that repo is not commit-safe — see the report). Adds `tasks.reopened_count`/`reopened_at`, bumps them on every terminal → in-flight transition, prints `[REOPENED — reopened_count=N]`. |
| `backfill_reopened.py` | One-time audited seed of the column from the legacy `notes LIKE '%REOPENED%'` markers. `--audit` lists candidates; `--seed ID[:TS],... --apply` writes. Idempotent. |

Applied on 2026-08-23:

```
backfill_reopened.py --seed 433:2026-08-22T04:36:00,755:2026-08-22T04:36:00,760:2026-08-22T04:36:00 --apply
```

Re-applying the patch after a `wq` rewrite:

```
patch /home/lumen/bin/wq < wq_reopened_count.diff && python3 -m py_compile /home/lumen/bin/wq
python3 -m pytest harness/tests/test_wq_reopened.py
```
