# OPS-AUDIT A5: Lumen Contention Analysis — wq#755

**Author:** claude-web-755-760  
**Date:** 2026-08-23  
**Campaign:** FLEET_OPS_AUDIT_2026-08-21 (R5/F4)

## 1. Question

Is "all builds run on Lumen" still affordable now that the fleet has 15 nodes, or
is the T5810 a bottleneck that limits dispatcher concurrency?

## 2. Methodology

Live measurement from /proc, sar, vmstat, iostat, ps, and docker on Lumen at
2026-08-23 ~05:42 UTC. 2 fworkers (wq#786, wq#787) were running concurrently at
sample time plus 1 LPP live_agent process, ~32 docker containers, and this MCP
session. The foreman dispatcher had just spawned 3 workers in the 05:36 pass and
3 more in the 05:43 pass (ceiling=3). No artificial load test was injected — this
is an observational characterization. Historical sar data covers the prior 24h.

## 3. Findings

### 3a. CPU (44 HT cores, E5-2699 v4 @ 2.20GHz)

| Metric | Value | Assessment |
|---|---|---|
| Load average (1/5/15m) | 22.28 / 18.28 / 11.76 | Elevated but <44 (core count) |
| sar %user 24h avg | 10.31% | Light |
| sar %system 24h avg | 2.59% | Light |
| sar %idle 24h avg | 86.24% | Abundant headroom |
| PSI cpu some avg300 | 0.00% | Zero CPU starvation |
| PSI cpu full avg300 | 0.00% | Zero full-core stall |

**Verdict:** CPU is NOT the bottleneck. Even at load-avg 22, PSI shows zero
starvation — the 44 HT cores absorb bursts without queueing. Fworkers are
IO-bound (API calls), not compute-bound.

### 3b. Memory (64GB physical, 24GB swap, swappiness=10)

| Metric | Value | Assessment |
|---|---|---|
| Total RSS (all procs) | 9,194 MB | 15% of RAM |
| Available | 48 GB | 80% free |
| Swap used | 8.5 GB (35%) | Chronic but stable |
| PSI memory some avg300 | 0.00% | Zero memory pressure |
| PSI memory full avg300 | 0.00% | Zero reclaim stalls |
| sar kbswpused 24h avg | 8.7 GB | Steady-state, not growing |

**Swap interpretation:** 8.5GB swap used despite 48GB available = cold pages from
long-lived containers (ClickHouse, Langfuse, Dify stack) paged out by swappiness=10.
This is CORRECT behavior — swap is doing its job reclaiming cold pages. PSI confirms
zero reclaim pressure. The swap usage is NOT a symptom of overcommit.

**Top consumers:**
- ClickHouse (Langfuse): ~1,502 MB RSS
- Dify stack (next-server + workers + api + celery + sandbox): ~2,100 MB
- MinIO: 428 MB
- Rerank server: 329 MB (UNUSED per wq#569 audit — 0 call sites)
- Netdata: 293 MB
- Docker infrastructure (dockerd + containerd + proxies): ~900 MB
- MCP gateway/Commander: ~280 MB
- Ollama (idle, CPU-pinned): 105 MB

**Verdict:** Memory is NOT the bottleneck. Each fworker (claude-code) uses ~440 MB
RSS. At ceiling=5 that's ~2.2 GB incremental — still leaves 47 GB available. The
8G MemoryMax on fworker systemd-run units (from wq#380) is well-calibrated.

### 3c. IO (7 NVMe drives in md0 RAID5 + md1 RAID0)

| Metric | Value | Assessment |
|---|---|---|
| sar %iowait 24h avg | 0.29% | Negligible |
| iostat %util boot drive | 1.2% (sample) | Light |
| iostat md0, md1 | 0% at sample | Zero contention |

**Verdict:** IO is NOT the bottleneck. RAID arrays barely touched by fworkers.

### 3d. MCP Gateway / Commander Path

| Metric | Value | Assessment |
|---|---|---|
| /health response | 200 in 1.2ms | Healthy |
| pressure-guard firings (24h) | 0 restarts | No shed events |
| Commander node RSS | 174 MB | Normal |
| Supergateway RSS | 109 MB | Normal |

**Verdict:** MCP path NOT degrading at current concurrency. The wq#380 8G-shed
mechanism has not fired. Historical failure mode (memory pressure) fixed by 64GB upgrade.

### 3e. Historical Dispatch Pattern

Dispatcher runs ~20 min, spawns up to 3/pass (ceiling=3). Both recent passes spawned
3 and hit cap. Tickets skipped for "runaway_stuck" (5 attempts) — a worker
success-rate problem (F3), not contention.

## 4. Safe cloud_ceiling Recommendation

| Metric | Ceiling=3 | Ceiling=5 | Ceiling=7 |
|---|---|---|---|
| Incremental fworker RAM | baseline | +880 MB | +1,760 MB |
| Available after | 48 GB | 47.1 GB | 46.2 GB |
| CPU cores per worker | 14.7 | 8.8 | 6.3 |
| PSI risk | zero | zero | low |

**Defensible safe ceiling: 5.** At 5 concurrent fworkers: ~12 GB total RSS, 47 GB
available, 8.8 HT cores per worker (API-bound workers barely use 1). 5×8G MemoryMax
= 40G theoretical max, under 48G available.

**Stretch ceiling: 7.** Feasible on paper but enters the zone where concurrent build
+ LPP + rerank + Netdata + ClickHouse cache could cause swap pressure. Not recommended
without PSI monitoring wired as a tight_mode trigger.

## 5. Observable That Should Trigger tight_mode

Current tight_mode trigger: budget-based (governor pressure). For contention, add:

1. **PSI memory some avg60 > 5%** → tight_mode (max_spawns=1, min_pri=1)
2. **PSI memory some avg60 > 15%** → hard stop (no new spawns until clear)
3. **PSI memory full avg60 > 0%** → hard stop + ntfy alert

Implementation: pressure-guard timer already reads /proc/pressure/memory — add a
JSON state file, have dispatcher check at candidate-scan.

## 6. Work Classes That Could Safely Move Off Lumen

| Work Class | Host | RSS | Action | Risk |
|---|---|---|---|---|
| lumen-rerank.service | Lumen | 329 MB | **STOP** (0 callers per wq#569) | None |
| Research indexing | Lumen | varies | Move to Thoth/Bes | Low |
| Dify stack | Lumen | ~2.1 GB | Candidate for Bes/Sobek | Medium |
| LPP live_agent | Lumen | ~270 MB | Move to Isis | Low |

## 7. Conclusion

**"All builds run on Lumen" is still affordable.** The T5810 at 64GB with 44 cores
is running at ~15% memory and ~13% CPU utilization with 2 fworkers + 32 containers.
The bottleneck is NOT hardware contention — it is worker success rate (F3) and
dispatcher ceiling (currently 3, safely raisable to 5). The rule is not hurting; the
config is just set too low.

## P0/P1 Findings

- **P1: lumen-rerank.service runs an UNUSED 329 MB rerank model.** 0 call sites in
  lumen_recall2.py (confirmed wq#569). Recommend: stop and disable. Frees 329 MB
  immediately. → **Seed ticket.**
- **P1: cloud_ceiling=3 is artificially low.** PSI data shows zero contention at
  current load. 5 is defensible; recommend raising after this report is reviewed.
  → **Seed ticket.**
- **P1: No PSI-based tight_mode trigger exists.** Budget governor guards against
  subscription overuse, not box overload. A contention-triggered tight_mode prevents
  the MCP gateway degradation that wq#380 hardened against. → **Seed ticket.**
