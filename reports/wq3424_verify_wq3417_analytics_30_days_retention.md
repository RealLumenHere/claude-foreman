# wq#3424 verify: wq#3417 [cf-changelog] "Analytics - 30 days on every plan"

Archetype 8 adversarial verify-pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5@high
(fresh instance). Result: **CONFIRMED** — no contradiction, no escalation.

## What wq#3417 claimed

A pure triage ticket (`wq show 3417`, archetype `rca-investigation`): no code change, no PR.
Four factual claims live in the ticket's `notes` field:

1. The changelog (`developers.cloudflare.com/changelog/post/2026-10-02-30-days-analytics-on-every-plan/`)
   says zone **adaptive** analytics datasets (HTTP requests, security events, DNS) now
   retain ≥31d on Free/Pro, queryable 30d per GraphQL request; previously 24h–8d
   depending on dataset.
2. Lumen's two zones, `lumennode.uk` and `tiny-whale.com`, are both plan `Free Website`
   (verified via a `/zones` probe, `/tmp/fworker-wq3417/zone_plans.py`).
3. Impact: wq#3365's zone-side `httpRequestsAdaptiveGroups` cross-check of the wq#3158
   2026-09-29T19:02Z window is now queryable until ~2026-10-30 19:02Z, not 2026-10-02 —
   and this was posted as a note on wq#3365. Workers Logs `telemetry/query` 3-day
   retention is **not** affected (separate product from the GraphQL Analytics API).
4. No Lumen code anywhere in `foreman/bin/agent/kb` assumes the old 24h–8d cap — only the
   wq#3158 probe scripts touch adaptive datasets, using 48h windows.

## What was checked

1. **Changelog text, fetched fresh** (`fleet-search read`, live HTTP 200, 61926 bytes):
   > "Adaptive analytics datasets, such as HTTP requests, security events, and DNS
   > analytics, retain at least 31 days of data for Free and Pro domains, and you can
   > query up to 30 days in a single request. Previously, Free and Pro domains could see
   > between 24 hours and 8 days of history depending on the dataset."

   Matches claim 1 verbatim. The page also states "This change does not alter which
   datasets or fields your plan can access. Aggregated datasets, such as
   `httpRequests1hGroups`, keep their existing per-plan limits" — confirms the change is
   scoped to *adaptive* datasets only, which is exactly how wq#3417 scoped its impact
   claim (not a blanket "all analytics" claim).

2. **Zone plan, re-probed live** (not reused from the now-gone `/tmp` script — wrote an
   independent read-only `GET /zones` probe, `~/.cf_workers_token`, prints zone name +
   plan only): `lumennode.uk plan= Free Website`, `tiny-whale.com plan= Free Website`.
   Matches claim 2. Falsifiable: a plan upgrade since 2026-10-02 15:46 would have printed
   something other than `Free Website` and broken the claim — it didn't.

3. **wq#3365 note, re-read from the live ticket** (`wq show 3365`): the
   `[2026-10-02 15:47:57] wq#3417 (CF changelog 2026-10-02): ...` note is present
   verbatim, including the explicit carve-out "Workers Logs telemetry/query retention is
   NOT covered by this change (still 3d Free); that part of this task still ages out
   ~2026-10-02 19:02Z." Matches claim 3 exactly, including the correct non-conflation of
   Workers Logs (Observability product, `telemetry/query`, separately gated by wq#3365)
   with zone/account Analytics (GraphQL Analytics API, the thing the changelog changed).

4. **Grep claim, reproduced independently** across `/home/lumen/foreman`, `/home/lumen/bin`,
   `/home/lumen/kb`, `/home/lumen/agents/work-queue`, `/home/lumen/mailbot` for the actual
   CF GraphQL adaptive-dataset identifiers (`httpRequestsAdaptiveGroups`,
   `securityEventsAdaptive`, `dnsAnalyticsAdaptive`, `workersInvocationsAdaptive`,
   `AdaptiveGroups`): the only hits are `reports/wq3158/*.py` (6 probe scripts, confirmed
   48h windows — e.g. `cf_gql_probe.py`'s `SINCE = "2026-09-29T19:02:00Z"` /
   `UNTIL = "2026-10-01T19:02:00Z"`), `reports/wq3158_workers_logs_attribution.md`,
   `reports/wq3366_verify_wq3158_workers_logs_attribution.md` (a verify report, not new
   code), and one `kb/ISSUE-LOG.md` row (#1157) documenting the same wq#3158 finding.
   No other script, runbook, or kb entry references these datasets or hardcodes a
   24h/8-day assumption. Matches claim 4 exactly.

## Attempted breaks (none landed)

- Checked whether the changelog's "does not alter which datasets or fields your plan can
  access" language undercuts the impact claim on wq#3365 — it doesn't; the impact claim
  is about *retention window*, which the changelog explicitly extends, not about
  *dataset access*, which it explicitly leaves alone.
- Checked whether "Workers Logs unaffected" could be wrong because the changelog also
  mentions "Account-level analytics are under Observability > Analytics" — read the
  full page: that line is about where account-level *GraphQL Analytics* datasets are
  surfaced in the dashboard, not about the Workers Logs `telemetry/query` Observability
  API wq#3158/wq#3365 are blocked on. Different API, different product, confirmed by
  wq#3158's own finding (403 on `telemetry/query`, unrelated to zone analytics scope).
  No conflation.
- Re-ran the zone-plan probe instead of trusting the (now-deleted, `/tmp`-ephemeral)
  original script path — independent falsifiable re-derivation, not just citing the
  prior run.

## Minor observation (not a contradiction)

wq#3417 left no repo artifact (no `reports/wq3417_*.md`, no commit) — its sibling
same-day triage ticket wq#3413 did commit a `reports/3413_*.md` write-up. Everything
wq#3417 produced lives in the ticket's own `notes` field plus the note it pushed to
wq#3365. This is a documentation-thoroughness gap, not a factual error, and doesn't
change the verdict — flagging only so the inconsistency doesn't look like an oversight
if someone later searches `reports/` for a wq3417 write-up and finds nothing.

## Verdict

**CONFIRMED.** All four factual claims reproduce independently against live sources
(the changelog page, the CF API, the live wq#3365 ticket, a fresh repo grep). No
contradiction found; no escalation to judgment.
