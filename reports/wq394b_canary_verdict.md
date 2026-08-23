# wq#797 — netdata canary: 48h watts/CPU gate verdict + fleet-rollout recommendation

Canary node: **lumen** (T5810, tailnet `100.117.66.102`), deployed by wq#394
2026-08-22 ~06:41 UTC. Soak marker `/var/lib/wq394-netdata-canary/soak_start`
recorded 2026-08-22 06:54:26 UTC. This ticket's due date (2026-08-24 06:41Z)
is the formal 48h mark; this session ran at **2026-08-23 06:38 UTC, 23.8h
into the 48h window** (ticket was dispatched ~24h ahead of the soak
completing). `analyze.py` correctly refuses a verdict before 48h have
elapsed, so the numbers below are a `--min-hours 0` sanity-check run against
the same accumulated data, not the formal gate output.

## Bug found and fixed: `netdata_cpu_pct` was never normalized by core count

`analyze.py`'s stock output at 23.8h says **HOLD**:

```
avg netdata CPU share of total: 24.741%
estimated netdata power draw (pkg_watts * cpu_share): 7.760W
RECOMMENDATION: HOLD (thresholds: cpu<2.0%, watts<3.0W)
```

24.7% average CPU looked implausibly high for a steady-state daemon (the
wq#394 report already flagged ~25% as an unrepresentative post-install ML
training burst) and didn't decay over the 23.8h window — min 21.8%, max
26.2%, narrow and flat. That's not what a transient burst looks like; it's
what a unit conversion bug looks like.

`sampler.py` computes `netdata_cpu_pct` as
`100 * (systemd CPUUsageNSec delta in seconds) / wall_seconds` — the same
convention `top`'s `%CPU` column uses, where 100% means one full core
saturated and the range is `0..100*ncpu`, **not** a share of total system
capacity. `total_cpu_pct` in the same CSV *is* divided by `ncpu`, so the two
columns aren't apples-to-apples, and `analyze.py` was comparing the
un-normalized per-core value directly against a "share of total" threshold.

The host (`lumen`, T5810) has **44 logical cores** (dual Xeon E5-2699 v4,
verified via `lscpu` / `os.cpu_count()` in the same root context the sampler
runs in). Dividing by 44 gives the actual system-wide share:

```
avg netdata CPU (single-core-equivalent, like top's %CPU): 24.741%
avg netdata CPU share of total (44 cores): 0.562%
estimated netdata power draw (pkg_watts * cpu_share): 0.177W
RECOMMENDATION: GO (thresholds: cpu<2.0%, watts<3.0W)
```

Fixed in `ops/wq394-netdata-canary/analyze.py`: normalizes `netdata_cpu_pct`
by `os.cpu_count()` before comparing to the threshold, and prints both the
raw per-core figure and the normalized system-share figure so the next
person doesn't have to re-derive this. `sampler.py`'s raw CSV column is left
alone — its current semantics (per-core %) are legitimate and match how it's
labeled once `analyze.py` does the normalization; changing the sampler's
output format mid-collection would make the already-logged 286 rows
inconsistent with anything collected after the change.

Secondary, non-blocking finding: `total_cpu_pct` in the CSV is effectively
meaningless as implemented — it sums *all* `/proc/stat` jiffy fields
(including idle) and divides by the theoretical max, so it measures "how much
wall-clock time got accounted for" rather than "how busy the host is," and
reads ~98-99% on every row regardless of actual load. It isn't used by the
gate formula, so it doesn't affect this recommendation, but it's misleading
if anyone reads the raw CSV directly. Not fixed here — out of scope, no gate
dependency, flagging for a future cleanup pass.

## Numbers (23.8h of data, 286 samples, 5min interval)

| metric | value |
|---|---|
| avg package watts (RAPL pkg-0, whole host) | 31.39W |
| avg netdata CPU, system-wide share (44 cores) | 0.562% (min 0.50%, max 0.60%) |
| estimated netdata power draw | 0.177W |
| avg netdata RSS | 501.6MB |

Both gate metrics are roughly **3.5x and 17x under threshold** respectively
(cpu 0.56% vs 2% limit, watts 0.18W vs 3W limit), with a narrow, flat
distribution across the whole 23.8h window — no burst, no drift, no decay.
That stability is itself informative: the number isn't catching netdata
mid-transient, it's the steady state.

Caveat: the wq#394 report references cross-checking against a pre-install
watts baseline but never recorded one numerically, so the 3W threshold above
is being read as an absolute estimated draw, not a delta from a measured
baseline. Given the corrected estimate is 17x under threshold, this gap
doesn't change the verdict either way.

## Recommendation: **GO**, pending the routine 48h re-confirmation

Fleet rollout recommendation: **GO**. netdata's steady-state resource cost
on this canary is well inside both budgets, by a wide and stable margin.

This is not the formal 48h gate output — that requires re-running
`ops/wq394-netdata-canary/analyze.py` (now fixed, no override needed) after
2026-08-24 06:41Z, which is a five-second check. Given the flatness of the
first 23.8h, that re-run is expected to reconfirm GO, but it should still run
before treating this as final — a fleet-worker session in this build lane
can't stay alive across the remaining ~24h to do it.

**RAPL power inversion, not this ticket's scope.** No open question about
CPU/watts overhead remains — the two questions below are the actual blockers
on a fleet-wide rollout decision, both requiring Frank:

1. **MCP capability scope.** `journal`/`procs`/`network-connections` MCP
   tools are cloud-claim-gated in netdata v2.11 — verified `execute_function`
   on `processes` returns `not authorized` even with a valid API key while
   unclaimed. Decision needed: accept a metrics/anomaly/alerts-only MCP
   surface as the fleet default, or revisit the no-cloud-claim constraint to
   get the process/log tools.
2. **Remote-write to Bes VictoriaMetrics.** Optional centralized metrics
   needs a Bes-side scrape-target or ingest-port change — VM's `8428` refuses
   connections from other tailnet hosts. Out of reach for a single-node
   worker session; same class of access wq#753 needed. Decision needed:
   whether/how to open that up, or run each node's netdata standalone
   without central aggregation.

Ref: `reports/wq394_netdata_canary.md` (deploy + MCP smoke test),
`ops/wq394-netdata-canary/analyze.py` (fixed gate script).
