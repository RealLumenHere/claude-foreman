# wq#3366 verify: wq#3158 mcpw Workers Logs Commander-drop attribution (post wq#2925 48h window)

Archetype 8 adversarial verify-pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5
(fresh instance, per the ticket's escalate-on-contradiction rule).

PR under test: `claude-foreman` PR #1572
(`http://100.121.17.5:3000/lumen/claude-foreman/pulls/1572`, merged as `0da8ac2b`, now an
ancestor of this branch's base `b4b08f3f`). The diff is report-only: `reports/wq3158/*.py`
(6 probe scripts), `reports/wq3158/timeline_48h.txt` (a captured run), and
`reports/wq3158_workers_logs_attribution.md`. No worker source changed.

## What wq#3158 claimed

The ticket (`wq show 3158`) asked for a **read-only** path-level Workers Logs query
(status>=400 by path; `/register` vs `/authorize-complete` vs `/token` 200 counts; `/mcp`
401 correlated with the wq#2918 restart), with an explicit fallback: "Close with the
finding either way." The author's finding:

1. The path-level query is blocked: `~/.cf_workers_token` gets 403 (`Authentication
   error`) on `telemetry/query` and `telemetry/keys`, and also lacks `zone.analytics.read`
   on both zones. Logging itself is confirmed live (`observability.enabled=true`,
   `logs.persist=true`, `head_sampling_rate=1`, survives 5 redeploys). A token-mint
   physical task was filed (wq#3365, `frank_gated`) rather than blocking the ticket on it.
2. As a fallback, Worker-side Account Analytics (which the token *can* read) was used to
   cross-check wq#3264's RCA: 31 minutes with a >=70s call in a 24h fixture window match
   wq#3264's 31/36 cancels with a 66-72s call in flight; `scriptThrewException` spikes are
   all deploy artifacts; the 35 `clientDisconnected` events are a periodic sub-2.2s client,
   not 70s Commander drops.
3. No evidence of the ticket's conditional KV-lag `/authorize` 400 retry was found (that
   evidence needs the blocked path-level logs), so no worker code was touched — correct
   per the ticket's own read-only framing and "close with the finding either way" clause.

## What I checked

**Ticket fidelity:** Read `wq show 3158` in full. The ticket is explicitly read-only
("WORK (read-only, no worker changes)") and permits closing on a finding alone. The PR
diff touches only `reports/`, confirmed via `git diff b4b08f3f FETCH_HEAD --stat` (8 files,
533 insertions, 0 deletions, no non-report paths).

**Re-ran every probe live, not trusted from the report** (`~/.cf_workers_token` is
readable from this session; outputs below are fresh, not replayed):

- `cf_obs_probe.py` -> `telemetry/query 403 [(10000, 'Authentication error')]`,
  `telemetry/keys` same — reproduces the core blocked-access claim exactly. (`scripts`
  now lists a 7th script, `lumen-mcp-v2-staging`, added after the PR — expected drift, not
  a contradiction. `verify` itself returns 401 "Invalid API Token", a line the report
  didn't quote; this is the documented Cloudflare quirk where `/user/tokens/verify` 401s
  for scoped account tokens even though the same token works for `/accounts` and
  `/accounts/.../workers/scripts` right after, which it does here — not evidence the
  token is broken for the account-scoped calls the report relies on.)
- `cf_obs_settings.py` -> both scripts show `observability.enabled=true`,
  `logs.persist=true`, `head_sampling_rate=1` — exact match.
- `cf_gql_probe.py` -> `workersInvocationsAdaptive`: lumen-mcp success=9098,
  clientDisconnected=35, scriptThrewException=10; lumen-mcp-v2 success=282,
  scriptThrewException=2 — exact match to the report's table. `httpRequestsAdaptiveGroups`
  on both zones still refused for missing `zone.analytics.read` — exact match.
- `cf_gql_minutes.py` for the wq#3264 fixture window
  (2026-09-30T02:41:12Z..2026-10-01T02:41:12Z) -> `minutes_with_long_call 54 {'70s': 24,
  '60-69s': 23, '75s': 7}` — exact match (54/24/23/7 all reproduce).
- `cf_gql_minutes.py` for the full 48h window (2026-09-29T19:02:00Z..2026-10-01T19:02:00Z)
  -> `minutes_with_long_call 72 {'75s': 16, '60-69s': 31, '70s': 25}` and
  `p999_seconds_hist {60: 5, 61: 17, 62: 3, 65: 1, 66: 5, 70: 25, 75: 16}` — byte-for-byte
  match to the report's quoted numbers.
- `cf_gql_timeline.py` for the full 48h window -> diffed against the committed
  `timeline_48h.txt` fixture: **identical, byte-for-byte** (`diff` exit 0). This is strong
  evidence the committed fixture is a genuine unedited capture, not a hand-written
  approximation.
- `cf_gql_long.py` -> deploy timestamps for `lumen-mcp`
  (02:25:22/02:26:25/02:27:16/02:27:23/03:33:53) match the report's cited cluster exactly.
  `lumen-mcp-v2`'s list has shifted (a new 19:12 deploy pushed the oldest of the report's
  cited six, 17:18:38, out of the API's 6-row window) — expected drift from a deploy that
  happened after the PR merged, not a contradiction.

**Arithmetic/consistency checks on the fixture:**
- `clientDisconnected` row count in `timeline_48h.txt` is 34 rows but one row carries
  count=2 (04:22), summing to **35** — matches "35" in the report. Wall times across all
  rows range 717ms-2212ms, matching the claimed "0.7 to 2.2 s" exactly.
- `scriptThrewException` rows sum to 10 (lumen-mcp) + 2 (lumen-mcp-v2) = 12, matching the
  report's table (10 and 2) and every exception timestamp falls inside a deploy-timestamp
  cluster, as claimed, except the two the report itself flags as unmatched (09-29 22:10,
  lumen-mcp-v2 16:00).
- `clientDisconnected` timestamps step in ~30-31 min increments through most of the
  window (e.g. 02:19->02:50->03:21->03:51->04:22, each +30-31 min), consistent with "a
  periodic ~1.5s client" rather than noise; gaps of 60-168 min appear where a cycle's
  request apparently didn't land in this status bucket, not a break in the pattern.
- Cross-ticket citation check: wq#3264's RCA (`reports/wq3264_mcpw_commander_drops_rca.md`)
  independently states "cancel seconds on :3100: 36" and "ANY in-flight call aged 66-72s:
  31/36 (86%)", and `TOOLS_CALL_TIMEOUT_MS = 70_000` at `router.ts:29`. Both numbers the
  wq#3158 report leans on (36, 31, 70s) are quoted correctly, not invented.
- wq#3365 (the physical token-mint task) exists, is `frank_gated` (not a blocking gate on
  this ticket), and its detail matches the 403/zone-analytics finding precisely.

**What I could not re-verify:** the 75s plateau's cause (worker source outside this
session's grant too — same gap the author hit) and the raw `/authorize` 400 path-level
counts (still blocked, same 403). Both are honestly flagged as unresolved in the original
report rather than guessed at.

## Verdict: CONFIRM

Every quantitative claim in the report reproduces on a fresh, independent re-run of the
same probes against live Cloudflare APIs — including one byte-for-byte fixture match
(`timeline_48h.txt`) and several exact numeric matches (35, 10, 2, 54/24/23/7, 72/25/31/16,
the full `p999_seconds_hist`). The one line the report omitted from its quoted output
(`verify 401`) is a known Cloudflare API quirk for scoped account tokens, not a sign the
probe or the token is broken — the same token succeeds on every account-scoped call the
report's conclusions depend on. The cross-reference to wq#3264 (36/31/70s) checks out
against that report's own text. The ticket was explicitly read-only and explicitly allowed
closing on a finding alone; the diff makes no worker change, which is correct given the
KV-lag retry's precondition (path-level 400 evidence) was never met. The physical
token-mint task (wq#3365) is correctly filed as a non-blocking physical dependency, not a
gate on this ticket.

Not re-implementing anything (ticket instruction: "Do NOT re-implement; verify"). No
escalation — confirming on the board per ticket policy for a non-contradiction.
