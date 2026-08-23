# wq#865 — netdata canary: what the 25% actually is, and what still needs deciding

Three reports now say different things about the same number. This one settles
it against live data, and the short version is that **everybody's measurement
was right and two of the three readings of it were wrong**.

| claim | source | verdict |
|---|---|---|
| ~25% is a transient post-install ML training burst, not representative | `wq394_netdata_canary.md` | **wrong** — it is steady state, and it is not ML |
| ~25% is netdata's share of the host, ~12x over the gate threshold → HOLD | wq#798 escalation (this ticket) | **wrong reading** — that figure is per-core, not host share |
| ~25% is a per-core figure; ÷44 cores = 0.56% host share → GO | `wq394b_canary_verdict.md` | **correct**, independently reconfirmed below |

The gate verdict is **GO** and does not change. What does change is the reason,
and two things nobody has gated on yet.

## The unit question, settled

`sampler.py` derives `netdata_cpu_pct` from the delta of systemd's
`CPUUsageNSec` over wall-clock seconds. That counter is CPU-time summed across
all cores, so the ratio is a single-core-equivalent percentage — `top`'s `%CPU`
convention, range `0..100*ncpu`.

wq#798's cross-check (`systemctl status netdata` reporting `CPU: 5h 53min` over
23.9h wall = 25.4%) is arithmetically the same quantity from the same counter.
It confirms the sampler is not lying, which was its stated purpose — but it
cannot distinguish per-core from host-share, because both readings produce that
identical number. So it does not support the HOLD conclusion drawn from it.

Direct cgroup measurement over a 60s window, reading
`system.slice/netdata.service/cpu.stat` `usage_usec`:

```
cgroup total = 25.23% of one core = 0.573% of host (44 logical cores)
```

`os.cpu_count()` on this host returns 44, matching `/proc/cpuinfo` and
`sched_getaffinity`. (`nproc` can disagree — it honours `OMP_NUM_THREADS`, and
returned 4 in one shell during this investigation. `analyze.py` uses
`os.cpu_count()`, which does not.)

## It is not ML — it is apps.plugin

wq#394 attributed the reading to ML training. Sampling every PID in the netdata
cgroup over 60s and aggregating `utime+stime` per process:

| process | % of one core |
|---|---|
| `netdata` (main, all threads) | 10.60 |
| `apps.plugin` | 8.42 |
| `go.d.plugin` | 2.13 |
| everything else (`sd-jrnl`, `scripts.d`, `spawn-plugins`, …) | <0.6 combined |

Inside the main process, per-thread over the same window: `P[cgroups]` 3.35%,
`PD[apps]` 1.65%, `P[cgroupsdisc]` 0.58%, `P[proc]` 0.57%, `P[proc netdev]`
0.57%, `HEALTH` 0.33%.

The ML threads are `ML/PREDICT` 0.55% and `TRAIN` 0.25% — **0.8% of one core,
about 3% of netdata's total CPU.** Turning ML off would not measurably move
this number. The cost is process and cgroup enumeration: `apps.plugin` walking
`/proc` plus the cgroups collector walking 372 systemd-unit charts.

Cardinality backs that up — `/api/v1/charts` reports **7,502 charts / 17,345
dimensions**, of which 3,556 are `app` charts. That is a function of how many
services and containers this box runs, not how many cores it has.

The earlier "ML training burst" framing has been left in place in
`reports/wq394_netdata_canary.md` with a pointer to this file, rather than
edited out, so the original characterization stays visible alongside the check
that falsified it.

## Two things the gate does not cover

**1. RSS is climbing and is not a gate criterion.** Over the 24.0h window
(288 samples), netdata's RSS went 329MB → 615MB, averaging 502MB. By thirds:
440MB / 508MB / 559MB. Slope over the final 5.9h is **+12.0MB/h and rising**,
not flattening. There is no memory threshold in `analyze.py` and nothing to add
one against — netdata's dbengine cache sizing is unset in
`netdata.conf.template`, so the working set has no configured ceiling. A
44-core box with 615MB of headroom to spare absorbs this; an N100 fleet node
may not. `analyze.py` now prints the trend so the hour-48 re-run surfaces it,
but **setting a memory budget is Frank's call, not this ticket's.**

**2. A 2%-of-host threshold is a much tighter absolute budget on small
hardware.** The gate is expressed as a share of total capacity, so what it
permits scales with core count:

| host | 2% budget | netdata's measured absolute cost |
|---|---|---|
| `lumen` (T5810, 44 cores) | 0.88 cores | 0.25 cores → passes, 3.5x under |
| N100 fleet target (4 cores) | 0.08 cores | would need to be **3.1x cheaper** to pass |

Passing at 0.56% here says very little about whether an N100 passes. The
drivers (`apps.plugin`, cgroups) scale with process and container count rather
than cores, and a fleet node running fewer services will be genuinely cheaper —
but by how much is unmeasured. `reports/wq415_sobek_n100_efficiency_canary.md`
has no netdata data. **Deciding whether the canary host generalizes to fleet
hardware, or whether a second canary on an N100 is needed before rollout, is
Frank's call.** The 44-core canary result should not be read as a fleet result.

## `total_cpu_pct` was ~100% by construction — fixed

Confirmed as reported. The old formula summed *all* `/proc/stat` fields
(idle included) and divided by `dt * clk_tck * ncpu` — the theoretical maximum
of that same sum, since every core is in exactly one state every tick. It
measured accounting coverage, not load, and read 98.71% mean across all 288
rows (min 96.91, max 99.20).

`sampler.py` now computes `busy / (busy + idle + iowait)` from the jiffy
deltas. Taking the ratio makes it independent of `clk_tck`, core count and
wall-clock drift. `guest`/`guest_nice` are excluded from `busy` because
`/proc/stat` already counts them inside `user`/`nice`.

Verified: on the same 3s window, old formula 98.72%, fixed 12.62% — against an
independent manual `/proc/stat` delta reading 10.5% busy. The state file gained
two fields; `load_prev` accepts the old 4-field form and blanks
`total_cpu_pct` for exactly one row rather than guessing.

**The running canary was deliberately not redeployed.** The soak has ~24h left,
and swapping the sampler mid-collection would leave one column with two
different meanings in one file. Nothing reads `total_cpu_pct` — `analyze.py`
uses only `netdata_cpu_pct` and `package_watts` — so a uniformly wrong column
is easier to reason about than a half-and-half one. Deploy the fix after the
soak closes, or at the start of the next canary.

## Also changed

`analyze.py` gained `--ncpu`. The normalization uses the *analyzing* host's
core count, which is silently wrong if a CSV is ever copied off-host for
analysis — a live hazard now that a fleet rollout means more than one host.

## State at close

Gate output at hour 24.0, both metrics well inside budget, distribution flat:

```
avg package watts (whole host, RAPL pkg-0): 31.42W
avg netdata CPU (single-core-equivalent, like top's %CPU): 24.748%
avg netdata CPU share of total (44 cores): 0.562%
avg netdata RSS: 502.4MB
netdata RSS trend: 329MB -> 615MB over the window, +12.0MB/h across the final 5.9h
estimated netdata power draw (pkg_watts * cpu_share): 0.177W
RECOMMENDATION: GO (thresholds: cpu<2.0%, watts<3.0W)
```

The formal gate still needs `analyze.py` re-run after 2026-08-24 06:41Z — that
is wq#797's, and this ticket does not pre-empt it. Given 24h of flat data it is
expected to reconfirm GO on CPU and watts. The RSS trend and the small-host
threshold question are the two items worth Frank's attention when it does.

Refs: `reports/wq394_netdata_canary.md`, `reports/wq394b_canary_verdict.md`,
`ops/wq394-netdata-canary/sampler.py`, `ops/wq394-netdata-canary/analyze.py`.
