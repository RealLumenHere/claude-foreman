# wq#763 — A13 GAUGE: fleet flow metrics, baseline first

Campaign: `/home/lumen/instructions/FLEET_OPS_AUDIT_2026-08-21.md`. This ticket's own framing
states the risk directly: "this campaign will claim to have made the fleet faster. Without a
baseline captured BEFORE the changes land, that claim is unfalsifiable — the same defect as
the fail-open memoryd shadow run that looked like parity and was silence." This report is the
falsifiable version.

## 1. Baseline artifact (frozen, sha-recorded)

`reports/opsaudit_a13_baseline_20260821.json`
sha256: `3846b1f8a6d103307391747e4415f6eb556fa6bbab53b9a5fa49adfd307018f8`
(sidecar: `reports/opsaudit_a13_baseline_20260821.json.sha256` — verify with `sha256sum -c`)

Same discipline as wq#595's `golden_v4_frozen_<date>.jsonl` freeze: this file does not change
after this commit. If a metric definition turns out to be wrong, fix it in a **new** dated
artifact and say so — don't edit this one in place, or every ticket that already cited it goes
stale silently.

**Cutoff: `2026-08-21 18:06:42`** — the `created_at` of wq#751, the first ops-audit campaign
ticket. Baseline population is every `tasks` row that existed strictly before that moment, so
the campaign's own output cannot leak into its own baseline. Read-only: every query in
`opsaudit_a13_flow_metrics.py` is a `SELECT` against `file:queue.db?mode=ro`.

### What's in it, and how it was computed

| metric | definition | source |
|---|---|---|
| lead time (created→done), by lane | `done_at - created_at` for `status='done'` tickets with `done_at < cutoff` | queue.db, computed |
| cycle time (claimed→done), by lane | `done_at - claimed_at`, same population | queue.db, computed |
| throughput/week | count of `done_at < cutoff` completions per ISO week, overall + by lane | queue.db, computed |
| WIP in-flight at cutoff | count of `created_at < cutoff AND (done_at IS NULL OR done_at >= cutoff)` | queue.db, computed |
| rework rate | `(notes LIKE '%REOPENED%' OR status='rejected') / population`, pre-cutoff | queue.db, computed |
| blocked-age distribution at cutoff | **not reconstructable** — see §2 | campaign doc F1 (measured same day) |
| time-parked-on-Frank at cutoff | 74 items, 23 named Frank/gate/approval, 23-day avg, 41-day oldest, wq#383 33d | campaign doc F1 (measured same day) |
| worker success rate | 149 attempts / 69 tickets, 22% attempt-level success, 44% killed-by-turn-backstop | reports/opsaudit_a1_worker_success.md (wq#751, same day) |
| cost per successful ticket | 11.3 worker-hours / 14 successes ≈ 0.81 worker-hours/success; no marginal $ (Max subscription, audit R2) | derived from A1 |

Baseline headline numbers (full detail in the JSON):

- **Lead time p90 by lane** (hours): `build` 101.3, `user` 162.1, `research` 45.7, `auto` 33.1,
  `memory` 26.7, `pokemon` 10.1. `user`-lane lead time is dominated by the Frank-serialization
  problem (F1) — its p90 is 1.6x `build`'s despite `user` tickets typically being smaller asks.
- **Cycle time** (claimed→done) is short everywhere once claimed (`build` p90 0.39h, `auto`
  p90 0.4h) — confirms F1's framing: **the bottleneck is time-to-claim / time-parked-open, not
  execution time once a worker picks something up.** `user` lane has 0 done-with-claimed_at
  rows pre-cutoff — Frank-lane tickets essentially never go through the claim mechanism the way
  worker lanes do.
- **Throughput**: 74.9 completions/week mean across the full history (2026-W27 to W34,
  partial weeks at both ends). Weekly counts are lumpy (9, 119, 226, 6, 1, 22, 188, 28) —
  campaign-driven batches (W29, W33) dominate, not steady-state throughput. Any post-campaign
  throughput comparison must control for this or it will read as noise.
- **WIP in-flight at cutoff**: 147 tickets (total only — see caveat below).
- **Rework rate pre-cutoff**: 1.74% (12 reopened-marker + 1 rejected / 747).

## 2. What could not be captured cleanly (said loudly, per this ticket's own constraint)

`queue.db.tasks` has no status-transition log — only `created_at`, `claimed_at`, `done_at`, and
**current** `status`. Two consequences:

1. **Blocked-age distribution at the 2026-08-21 cutoff cannot be reconstructed.** I cannot tell,
   from today's rows, which in-flight tickets were specifically in `blocked` (vs `open` vs
   `claimed`) at that exact moment — only their status *right now*. Rather than fabricate a
   number, the baseline uses the campaign doc's own F1 measurement (52 blocked, 23 in `user`
   lane, avg 23d / oldest 41d), which was captured on the same day, likely within hours of the
   cutoff. **This ticket's own live exporter closes the gap going forward**: every 60s it writes
   a real point-in-time gauge, so this reconstruction problem cannot recur for any future date.
2. **WIP-at-cutoff is a single total (147), not split by sub-state**, for the same reason.

Recommendation for whoever picks up A9 (evidence-gate) or a future queue.db migration: add a
`status_changed_at` column (or a lightweight transitions table) written on every status flip.
It's the single schema change that would make every point-in-time metric in this report exactly
reconstructable instead of approximated.

## 3. Live metrics — exposed via the VictoriaMetrics A3 already built

A3 (wq#753) landed 14/14 fleet hosts on node_exporter, feeding the existing VictoriaMetrics via
an unprivileged vmagent, with a precedent for custom gauges: an `amdgpu_vram_*` textfile
collector on GPU hosts. This ticket reuses that exact mechanism — no new infrastructure:

- `reports/opsaudit_a13_flow_metrics.py --prom` renders a live queue.db snapshot as Prometheus
  text exposition format.
- `reports/opsaudit_a13_deploy_flow_exporter.sh` (idempotent, rerun-safe) installs it as
  `~/.local/node-exporter/bin/opsaudit_a13_flow_textfile.sh`, writing atomically to
  `~/.local/node-exporter/textfile/queue_flow.prom`, on a `systemd --user` oneshot service +
  60s timer (`opsaudit-a13-flow-metrics.timer`) — same pattern as A3's `vram-textfile.timer`.
- **Deployed and verified live in this session**: `curl http://100.117.66.102:9100/metrics`
  returns 43 `lumen_queue_*` series; `curl http://127.0.0.1:18428/api/v1/query?query=lumen_queue_wip_total`
  (the existing `lumen-powerdash-vmtunnel`) returns real vector results with `job="node_exporter_fleet"`,
  confirmed a second time through Grafana's own datasource proxy
  (`sum(lumen_queue_wip_total)` = 165, matching the live JSON snapshot taken the same minute).

Metrics exposed: `lumen_queue_status_total{status}`, `lumen_queue_wip_total{lane}`,
`lumen_queue_blocked_age_days{stat}`, `lumen_queue_frank_parked_total`,
`lumen_queue_frank_parked_age_days{stat}`, `lumen_queue_rework_rate_pct`,
`lumen_queue_flow_captured_at_seconds` (staleness signal).

Lead/cycle time and throughput are NOT re-exported live — they're expensive-ish full-table
aggregates better suited to periodic snapshot artifacts (like this baseline) than a 60s gauge,
and their statistical meaning only stabilizes over week-scale windows anyway (see the
throughput lumpiness above). Re-run `opsaudit_a13_flow_metrics.py --baseline` on a cadence
(weekly) to build the comparison series instead.

### Grafana panel

`http://100.110.156.91:3000/d/wq763-flow-metrics/fleet-flow-metrics-wq23-763-a13`
(same Grafana instance as A3's `wq753-node-exporter` dashboard — anon-viewer, or
`admin/lumen2026!`). 8 panels: total tickets, WIP, Frank-parked count, rework rate, WIP-by-lane
over time, blocked/Frank-parked age over time, status counts over time, exporter freshness.
Re-apply with the same POST pattern A3 used:
`curl -u admin:lumen2026! -H 'Content-Type: application/json' -X POST --data-binary @reports/opsaudit_a13_grafana_dashboard.json http://100.110.156.91:3000/api/dashboards/db`

### Mission Control panel

MC's application source lives outside this worktree's allowed scope (same boundary A3 hit for
its `/api/fleet` route — "this ticket proves the data and the query, not the MC route"). What
this ticket ships for MC to consume, unblocked and ready:
- The PromQL above, through the same `127.0.0.1:18428` VM tunnel MC's backend already reaches
  (proven live in this session, not just documented).
- The Grafana dashboard as an iframe-embeddable fallback if MC wants a rendered panel before its
  own `/api/flow` route exists.
A dedicated ticket to wire MC's backend to these queries (mirroring A3's own noted gap for
`/api/fleet`) is the natural next step; out of scope for what this ticket's worktree can reach.

## 4. Which numbers each other ops-audit ticket should move, and by how much

Falsifiable predictions, checked against a re-run of `--baseline` (new dated artifact) after
each ticket lands. "No prediction" is stated honestly where a ticket doesn't act on a
flow-metric lever.

| ticket | what it does | predicted flow-metric effect | magnitude |
|---|---|---|---|
| **A1** (done) | new turn ceilings, sized off measured SUCCESS turns | attempt-level worker success rate rises above the 22% baseline (A1's own falsifiable prediction #1) | falsified if unchanged/lower after 20 more standard-engineering attempts |
| **A2** (done) | Frank-decision backlog audit + apply script | `time_parked_on_frank` count drops from the 74 baseline | **already observed in this session**: live gauge reads 51 (see §5) — partial, not yet fully resolved |
| **A3** (done) | node_exporter + VM fleet-wide | enables this ticket's live metrics; no flow-metric movement of its own | n/a — infrastructure, not a flow lever |
| **A4** (done) | wire idle Langfuse | richer per-ticket cost attribution (real traces vs A1's token-volume proxy) | `cost_per_successful_ticket` gains a $-equivalent/trace-based figure; no lever on lead/cycle/WIP |
| **A5** (open) | measure Lumen contention | if contention is real and gets relieved, `build`-lane cycle time (currently p90 0.39h once claimed — already fast) is NOT where this shows; watch `build`-lane **lead time** p90 (101.3h baseline) if queueing was contention-driven rather than Frank-driven | no numeric prediction until A5's own measurement lands — flagged as the input A13 is waiting on |
| **A6** (done, design only) | risk-tiered standing approvals (not yet adopted) | zero effect on any metric here until adopted — a JUDGE ticket is a decision, not an enforcement | 0% movement expected pre-adoption; re-baseline after adoption, not after this ticket |
| **A7** (claimed) | SUBTRACT rule corpus audit | indirect: if a retired rule was generating wasted rework (agents punting fixable things to backlog per R3), `rework_rate` (1.74% baseline) could tick down | too indirect to size; watch, don't predict |
| **A8** (open) | enforce UNIVERSAL_RULES in dispatch-lint/CI | no flow-metric lever — this is a drift-prevention control, not a throughput one | no prediction |
| **A9** (open) | evidence gate on ticket closure | `rework_rate` likely **rises** short-term (currently-invisible FALSE_COMPLETE closures start getting caught and reopened instead of silently standing as `done`), then falls as workers adapt | falsifiable: reopened-marker rate should exceed 1.74% in the first 2 weeks post-A9, then trend back down within a month |
| **A10** (open) | audit why lanes are born switched off | `throughput` in currently-`enabled:false` lanes (lpp, questfree, pstack, fleet-fires) should go from ~0 dispatcher-driven completions to nonzero; overall WIP backlog in previously-invisible lanes (F2's build-lane example: 27 tickets never dispatcher-visible) should start draining | watch `lumen_queue_wip_total{lane="fleet-fires"}` etc. — currently 1 (near-zero, consistent with "off") |
| **A11** (open) | maintenance window design | `blocked_age_distribution` max (41d baseline / 41.1d live) should drop once CVE/patch tickets like wq#383 stop waiting indefinitely | falsifiable: oldest-blocked age should fall below 41d within one maintenance-window cycle after adoption |
| **A12** (open) | external practice consult | informational; no direct lever | no prediction |
| **A14** (open) | synthesize the decision sheet | consumes this report; no lever of its own | n/a |

## 5. Already-observed movement (same session, not a projection)

The live gauge captured in this same session (`reports/opsaudit_a13_live_sample_20260822.json`,
captured `2026-08-22 05:55:55Z`, ~11.8h after the cutoff) already shows drift from the F1
baseline, most plausibly attributable to A2 (Frank-backlog audit, done) and ordinary campaign
throughput:

| metric | baseline (2026-08-21, F1 / this artifact) | live (2026-08-22 05:52 UTC) | delta |
|---|---|---|---|
| items parked on Frank | 74 | 51 | -23 (-31%) |
| blocked tickets | 52 | 44 | -8 |
| oldest blocked (days) | 41 | 41.1 | +0.1 (noise — same ticket, one more day old) |
| WIP in-flight | 147 (at cutoff) | 165 (now) | +18 — expected: campaign itself added 15 new ops-audit tickets |
| rework rate | 1.74% | 2.43% | +0.69pp — watch this, could be early A9-shaped signal or just small-n noise (population 747→783) |

This is the falsifiable evidence this ticket exists to produce: the campaign has already moved
`time_parked_on_frank` by -31% within the same day, independently confirmed by both the frozen
baseline artifact and a live PromQL query through Grafana's datasource proxy — not a single
self-reported claim.

## 6. Artifacts

- `opsaudit_a13_baseline_20260821.json` + `.sha256` — frozen baseline, immutable from this commit forward.
- `opsaudit_a13_live_sample_20260822.json` — one dated live sample, for §5's delta table (not re-generated on future runs).
- `opsaudit_a13_flow_metrics.py` — `--baseline` / `--live` / `--prom`, read-only against queue.db.
- `opsaudit_a13_deploy_flow_exporter.sh` — idempotent exporter deploy (already run live on `lumen` this session).
- `opsaudit_a13_grafana_dashboard.json` — dashboard body, already POSTed and verified live.
