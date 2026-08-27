# wq#964 — 401-burst attribution (:19/:49 stale-key caller)

## Verdict
The half-hourly 401 burst was the sia-news `news-intake` container on the sia node
(192.168.8.203) scoring news through Lumen LiteLLM with a key burned by the 08-23 rotation.
It was already fixed end-to-end by wq#949 on 08-26 ~21:00Z (rotated to the budgeted key
`sia-news-2`). No live defect remains. This ticket attributes, confirms, and closes it.

## Evidence
- Cadence: `/home/lumen/logs/litellm/auth-failures.jsonl` 401s cluster at minute :19 (265) and
  :49 (212). Onset 2026-08-23T03:27Z, last 2026-08-26T21:49:50Z. Bad/absent key, source
  172.20.0.1.
- 172.20.0.1 = `docker_default` gateway = the Lumen host (confirmed via `docker inspect`).
  litellm binds 127.0.0.1:4000, so the remote sia container reaches it through the host
  tailscale proxy and presents as the bridge gateway. That is why the caller looked host-local
  and why the fix had to touch the sia node, not a Lumen timer.
- Attribution: LiteLLM SpendLogs shows the exact :19:49-51 / :49:48-49 `/v1/chat/completions`
  burst is `key_alias = sia-news-2` (deepseek-v4-flash). The sia-news compose sets
  `LITELLM_BASE=http://100.117.66.102:4000`, `LITELLM_MODEL=deepseek-v4-flash`, `LITELLM_KEY`
  from sia's `.env`. Matches the CORE handoff line "sia-news live-401 (open since 8/23) closed
  end-to-end" (wq#949).
- Fix already in place: `sia-news-2` budget $2/30d, 52 reqs today, spend $0.006, all 200 OK.

## Confirmation
Zero real `401 Unauthorized` from any source since 2026-08-26T21:49:50Z: ~13.5h and ~27
half-hour cycles clean at close. Every :19/:49 cycle now returns 200. Note: an early
`grep '172.20.0.1.*401'` reported false 401s by matching source-port digits (e.g. `:40112`);
the status-anchored count (`401 Unauthorized`) is 0. This is the same fake-auth trap logged on
2026-08-20, avoided here by grepping the status field and reading the container log directly.

## ops-emergency "$1.79"
That figure is 30-day window spend on the retired gen-2 key `ops-emergency-2` (created 08-23),
with 0 requests today. It was superseded by `ops-emergency-3` in wq#949. Not ongoing.

## Separate finding — wq#1003
`lumen-model-watchdog` probes 5 rungs every 10 min using `LITELLM_MASTER_KEY` from
`/home/lumen/agent/.env`, which wq#719 SPEND-1 set to the ops-emergency key. So ~520 probe
reqs/day (User-Agent Python-urllib) land on `ops-emergency-3` instead of a dedicated key. That
is the real remaining spend-attribution gap. Low value and partly by design (a watchdog wants
an uncapped key), so it is filed for a design decision, not fixed here.

## aq
Resolved aq#846 and aq#847.
