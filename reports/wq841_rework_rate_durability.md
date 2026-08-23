# wq#841 — rework_rate needed a signal that cannot be erased

Escalated from wq#787 (verify-pass of wq#763 / PR#53), verdict CONTRADICT: a real defect in one
metric of an otherwise reproducible deliverable.

## The defect

`rework_rate` counted reopened tickets as `notes LIKE '%REOPENED%'`. `REOPENED` is freeform text
a human or agent writes into a ticket's notes. `wq done` runs
`UPDATE tasks SET status='done', ..., notes=?` — notes is **replaced**, not appended. So the
sequence that the metric exists to count is exactly the sequence that erases it:

1. ticket closes → `done`
2. someone reopens it, writes `REOPENED ...` into notes → counted
3. the ticket is worked and closed again → notes replaced → **no longer counted**

`created_at`, which the baseline population is keyed on, never changes. The ticket stays in the
denominator and silently leaves the numerator, so the artifact shows no trace of the loss. Left
alone, this metric trends toward zero regardless of actual rework — false evidence of
improvement, in the deliverable commissioned to make campaign claims falsifiable.

A second, smaller defect rides along: SQLite `LIKE` is case-insensitive for ASCII, so prose
counted too. Of the three rows matching the rule on 2026-08-23, two were false positives —
wq#645 (`wq#626/wq#603 reopened with corrected notes`, about *other* tickets) and wq#787 (a
report quoting the metric's own SQL).

## Evidence

Three independent observations, none of them a self-report:

**1. wq#787's rerun.** A byte-identical copy of the shipped script, same fixed cutoff:
frozen 2026-08-22 → `reopened_marker_in_notes=12`, `rework_rate_pct=1.74`; rerun 2026-08-23 →
`2` and `0.4`. All-time, the live gauge went 2.43% → 0.6% in under 24 hours.

**2. Everything else still reproduces.** Re-running the current script against the live queue.db
with the same cutoff and diffing field-by-field against the frozen baseline:

```
fields that differ: ['baseline_schema', 'rework_rate']
```

Lead and cycle time percentiles across all 10 lanes, throughput per ISO week, WIP at cutoff,
population, rejected count — all byte-identical, two days on. The methodology is sound; one
signal was not. That is what makes the divergence diagnostic rather than ambiguous.

**3. Two more markers vanished during this fix.** The first query of this session returned five
candidate rows (433, 645, 755, 760, 787). Fifteen minutes later, 755 and 760 were gone: other
lanes had re-closed them, and their notes read `http://…/pulls/89` where the marker had been.
The decay is not historical. It runs continuously, at the rate the fleet closes tickets.

## The fix

`tasks.reopened_count INTEGER DEFAULT 0` plus `tasks.reopened_at TEXT`, added additively by wq's
own migration (`init()`, alongside the wq#741 `block_reason`/`blocked_at` pattern). Every
pre-existing row starts at 0/NULL; no other reader of queue.db is affected.

wq bumps the counter on every **terminal → in-flight** transition, and only there:

| from | to | counted? |
|---|---|---|
| `done`, `rejected` | `open`, `claimed`, `blocked` | yes — this is rework |
| `claimed` | `open` (release, lease reap, session retire) | no — nobody finished it |
| anything | `done`, `rejected` | no — entering a terminal state is not rework |

Both real reopen paths go through it: `wq edit <id> --status open` (the path the fleet actually
uses — there is no `wq reopen`) and `wq release` on an already-closed ticket. `wq claim` cannot
reopen anything; it refuses a `done` ticket outright. The transition rule lives in one place,
`is_reopen()`, so the two call sites cannot drift.

A reopen is never silent: `wq edit`/`wq release` print `[REOPENED — reopened_count=N]`, the same
discipline wq#530 applied to lease reclaims.

### Why not append-only notes

The alternative fix was to make `wq done` append to notes instead of replacing them. Rejected:
notes is read by fworker, self_heal, the dashboard and the evidence gate, all of which expect the
last close's text, and an append-log would grow unboundedly for the sake of one metric. The
metric's problem is that it was reading a prose field at all. A counter fixes it in the place the
defect lives.

## Backfill: three seeded, everything earlier is gone

`ops/wq841-rework-durability/backfill_reopened.py` audits and seeds. wq's migration deliberately
backfills **nothing** on its own: the legacy rule matches prose, and seeding that into the column
we are about to call authoritative would bake the false positives in permanently.

```
backfill_reopened.py --audit                                    # candidates + marker context
backfill_reopened.py --seed 433:...,755:...,760:... --apply     # the classification, recorded
```

Three tickets were seeded at `reopened_count=1, reopened_at=2026-08-22 04:36:00` — wq#433,
wq#755 and wq#760, all reopened in the same turn-ceiling recalibration, all carrying the marker
`|| REOPENED 2026-08-22 04:36:00 after turn-ceiling recalibration (PR#43)`. 755 and 760 were
seeded from evidence captured in this session minutes before their notes were overwritten.
wq#645 and wq#787 were rejected as prose. The script is idempotent — a row already above zero is
skipped, not re-counted — so a re-run after a crash converges.

**Everything before 2026-08-22 is unrecoverable, and the series says so.** The oldest queue.db
backup (`/home/lumen/backups/queue.db.bak.20260814_193619_pre_wq569_subsys`) predates the marker
convention and contains zero markers; the July backup likewise. There is no status-transition log
in queue.db — the same gap wq#763 already documented for blocked-age reconstruction. So roughly
14 lost reopens cannot be reconstructed from any surviving source, and the honest move is a
series that starts at a stated epoch rather than one that implies a history it does not have.

`REWORK_EPOCH = 2026-08-23`. Every emitted rework block carries the caveat inline, in the JSON,
not only in this report.

## What the frozen baseline says now

`reports/opsaudit_a13_baseline_20260821.json` and its `.sha256` are **untouched** — the freeze is
the point of the artifact, and the file still verifies. Beside it now sits
`opsaudit_a13_baseline_20260821.errata.json`: which fields still reproduce, which one does not,
why it moved, and what replaced it. `--baseline` output is `baseline_schema: 2`, identical to v1
in every field but the rework block.

In `opsaudit_a13_flow_metrics.md`, the 1.74% headline is struck through and retracted as a
measurement, and §5's rework delta row is voided — both sides of that `+0.69pp` were computed
from the erasable marker, so the delta measured note churn.

## Gauges

`lumen_queue_rework_rate_pct` now derives from the durable count. Two gauges join it:

- `lumen_queue_reopened_total` — the raw count, monotonic per ticket. A rate that falls is only
  good news if this held or fell too.
- `lumen_queue_reopened_signal_is_structural` — a tripwire, not a statistic. 1 while the count
  comes from `reopened_count`; 0 the moment the exporter falls back to the substring rule. A
  rework rate dropping while this reads 0 is the metric decaying, which is precisely the failure
  that went unnoticed the first time.

The JSON keeps `reopened_structural` and `reopened_marker_in_notes` side by side and never sums
them, so the gap between the two signals stays visible to a reader.

## Verification

- `harness/tests/test_wq_reopened.py` — 6 tests driving the real `wq` binary against a scratch
  DB. The load-bearing one, `test_reclose_overwrites_notes_but_never_the_count`, walks the exact
  decay path: reopen, close with a marker, reopen, close with unrelated notes; asserts the marker
  is gone from notes and the count reads 2. Others pin that an ordinary release is not rework,
  that `rejected → open` is, and that editing other fields never moves the counter.
- `harness/tests/test_wq_reap.py` (4) and `dispatcher/tests/test_self_heal.py` (11) — the other
  suites that drive wq — pass unchanged.
- Live, end-to-end: `--prom` → `~/.local/node-exporter/textfile/queue_flow.prom` →
  `curl http://100.117.66.102:9100/metrics` → VictoriaMetrics
  (`lumen_queue_reopened_total` = 3, `lumen_queue_reopened_signal_is_structural` = 1, queried
  through the existing `lumen-powerdash-vmtunnel`). Dated snapshot:
  `reports/wq841_live_sample_20260823.json`.

Pre-existing failures elsewhere in the repo suite (`test_shim_matrix`,
`test_fworker_integration`, `test_local_rung`, `test_dashboard_data`) are untouched by this
change and unrelated to it: the first three assert on fworker prompt text, and
`test_dashboard_data` fails inside `dashboard_data.queue_depth` on
`fd.load_config()["lanes"]` returning a tuple, before any wq call. Two of the four suites never
invoke wq at all.

## Deployment state

`/home/lumen/bin/wq` was patched live (backed up to `wq.bak.20260823_pre_wq841`, `py_compile`
clean, smoke-tested against a scratch DB before the live DB saw it). The schema migration applied
itself on the next `wq` invocation. As with wq#741, the change is **not** committed to the
lumen-bin git repo: that repo's working tree carries unrelated pre-existing drift from other
lanes, and committing would sweep it in. The authoritative copy of the diff is this PR's
`ops/wq841-rework-durability/wq_reopened_count.diff`.

The flow exporter was redeployed with the existing idempotent script
(`opsaudit_a13_deploy_flow_exporter.sh`), which is the sanctioned path for any change to
`opsaudit_a13_flow_metrics.py`.

## Follow-ups not taken here

- The Grafana dashboard body was not re-POSTed. Its rework-rate panel will show a step at
  2026-08-23 where the signal changed; it wants a dashboard annotation and a second panel for
  `lumen_queue_reopened_total`. Left out deliberately — wq#787 scoped this escalation to the
  rework signal, and re-POSTing a shared dashboard is a wider change than that.
- `reopened_count` is per-ticket and monotonic, so `sum(reopened_count)` (total reopen *events*)
  is available but not yet exported. Worth a gauge once the series has enough history to be
  worth plotting.
