# wq#4238 — EVALUATE: Loki+Promtail for centralized fleet log aggregation

**VERDICT: DO NOT ADOPT. The exact need this ticket describes — ship every node's logs
to Lumen/Bes, query across all 8 nodes with one Grafana panel — is already solved, live,
and lighter than Loki+Promtail would be. Adopting Loki+Promtail now would stand up a
second, heavier log pipeline next to the one already running. No code changed; this is
a research verdict plus the live evidence behind it.**

## What's already running (confirmed live this session, read-only)

`reports/wq857_victorialogs_bes.md` evaluated this same need in 2026-08 and picked
VictoriaLogs over Loki, citing an earlier tooling consult's ranking (same vendor as the
VictoriaMetrics already on Bes, ~1.3 GB RAM estimate vs 6-7 GB for Loki, single binary,
no external deps). `ops/wq857-victorialogs-bes/` staged the installers; wq#879/wq#874
closed the deploy-access question. It has since gone live — this session confirmed it
directly, not from the report:

- `curl http://100.110.156.91:9428/metrics` (Bes, tailscale address from the wq#857
  report) returns live VictoriaLogs metrics: `process_resident_memory_bytes` =
  112,623,616 (~107 MB RSS), `vl_data_size_bytes{type="storage"}` = 741,274,309 (~707 MB
  of log data retained).
- A LogsQL query (`* | stats by (host) count()` against
  `http://100.110.156.91:9428/select/logsql/query`) returned counts for 8 distinct
  hostnames shipping logs right now: `lumen` (22.2M), `bes` (14.9M), `khepri` (9.0M),
  `hathor` (3.0M), `isis` (1.3M), `anubis` (1.0M), `thoth` (0.96M), `sobek` (0.93M) —
  exactly the "search logs across all 8 nodes with one query" outcome this ticket asks
  Loki+Promtail to deliver.
- Grafana lives on Bes (confirmed reachable at `:3000` in the wq#857 baseline), not on
  "Spectrio" as this ticket's description states — `Spectrio` in this repo refers to a
  separate project (`projects/spectrio-tf1/`, a TF1 kiosk deploy), not a fleet log/metrics
  host. The ticket's placement instruction doesn't match any real host; treating it as a
  stale/mistaken detail rather than a requirement to relocate the existing Grafana.

## Resource comparison

| | VictoriaLogs (deployed) | Loki + Promtail (this ticket) |
| --- | --- | --- |
| Central store RAM | ~107 MB RSS measured live on Bes | Grafana's own sizing guidance and the wq#857-cited consult both put single-binary Loki at several GB once it's holding real retention, and production deployments split it into separate ingester/querier/compactor processes — more moving parts, not fewer, at fleet scale |
| Per-node agent | `systemd-journal-upload`, a stock Debian/Ubuntu package already on every node (no new binary to track) | Promtail: a separate Go binary per node, one more process to update/crash-loop across 8+ homelab-class nodes (several are resource-constrained N100s, per `reports/wq415_sobek_n100_efficiency_canary.md`) |
| Vendor fit | Same vendor (VictoriaMetrics) as the VictoriaMetrics instance already on Bes — one query language, one upgrade cadence | New vendor family, separate from the existing metrics stack |
| Ingestion path | Native `/insert/journald` endpoint: automatic `_time`/`level`/stream fields from journald, no field mapping to write | Promtail needs label/pipeline config per source to get comparable structure |

This session could not re-run an independent external resource-requirements search —
`fleet-search web` returned `all general web-search engines suspended/blocked (brave,
duckduckgo, google cse, startpage); ... deepseek fallback 402 (account balance
exhausted)` (logged as ISSUE-LOG row 1424). The comparison above rests on the live
measurement taken this session plus the prior internal consult's figures as reported in
`reports/wq857_victorialogs_bes.md:12-14`; it does not re-derive Loki's footprint from a
fresh outside source. Given VictoriaLogs is already deployed and meeting the ticket's
stated goal, re-confirming Loki's exact RAM number was not worth burning more search
budget on — the question that matters (do we need a second pipeline) is answered either
way.

## Recommendation

Close this ticket as evaluated-no-action. Do not install Loki or Promtail on Lumen or
any test node. If a future gap shows up that VictoriaLogs/journald genuinely can't
cover (e.g. a container's stdout that never reaches journald), re-open against that
specific gap rather than re-running this comparison — `reports/wq857_victorialogs_bes.md`
line 110-112 already names Vector as the documented fallback for that case, not Loki.
