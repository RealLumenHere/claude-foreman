# GEMINI_ACCESS.md — Gemini programmatic access for fleet research (wq#2627, closes wq#2580)

2026-09-25. Resolves the sanctioned programmatic path for Gemini as a fleet research
backend, replacing the free-tier `gemini-flash` rung that fails 51% of live calls
(wq#2580: 755/1478 calls over 30d were 429 RESOURCE_EXHAUSTED — the free API tier caps
`gemini-2.5-flash` at 20 requests/day, not the 250/day wq#233 recorded in 2026-07).

## Verdict

**Recommend path (b): paid AI Studio API key (usage Tier 1) through LiteLLM's native
`gemini/` provider.** It is the only path that is simultaneously sanctioned for
third-party clients, uncapped by RPD, and a drop-in for the existing gateway wiring.
Paths (a) and (c) — Gemini CLI / Antigravity OAuth — are **disqualified as LiteLLM
backends regardless of Frank's subscription tier**, because Google's terms restrict that
quota to the first-party clients and accounts are being suspended for routing it through
third-party tools (evidence below). The "which tier is Frank on" question therefore does
NOT gate this ticket; it only decides whether Frank *additionally* gets a nice interactive
CLI allowance for his own terminal use.

## The three candidate paths, verified 2026-09-25

All fetches via `fleet-search read` (fleet-search *web* is down fleet-wide — every SearXNG
engine suspended and no BRAVE_API_KEY, wq#2595; harness WebSearch/WebFetch are not granted
to BUILD workers, noted in final summary).

### (a) Gemini CLI Google-account OAuth — ALIVE in some form, but CLI-only. NOT wirable.

- The gemini-cli README (`raw.githubusercontent.com/google-gemini/gemini-cli/main/README.md`,
  fetched live) still lists "Sign in with Google" as Option 1 and claims a free tier of
  60 RPM / 1,000 RPD — so the ticket's premise "Google killed the free personal-login path
  2026-06-18" could not be independently confirmed; the README may be stale, but the login
  option still ships.
- `cloud.google.com/gemini/docs/quotas` (live): Gemini CLI + Code Assist agent-mode quota
  is per-user-per-day, **1500/day (Standard edition) / 2000/day (Enterprise edition)** —
  matching the ticket's AI Pro/Ultra figures. The quota table lists ONLY the two paid
  editions; no free row.
- **Disqualifier, independent of tier:** Google suspends accounts for using this quota
  outside the first-party clients. HN: "Google Antigravity TOS: 3rd party usage can get
  Google account suspended" (news.ycombinator.com/item?id=49553471, ~2026-09), and
  r/google_antigravity reports of bans for driving the OAuth endpoints from third-party
  tools (old.reddit.com/r/google_antigravity/comments/1qykskz). Wiring CLI-OAuth
  credentials into LiteLLM is exactly that pattern — same risk class as scraping
  gemini.google.com sessions, which the ticket already forbids. Frank's personal Google
  account is not an acceptable thing to gamble on a ToS ban.
- Disposition: fine for Frank's own interactive terminal use if his tier supports it;
  never a fleet backend. Tier question moves to the physical-task note, non-blocking.

### (b) Paid AI Studio API key, usage Tier 1 — RECOMMENDED.

- `ai.google.dev/gemini-api/docs/rate-limits` (live): Tier 1 requires only "set up and
  link an active billing account"; upgrade from Free is instant; spend-based rate limit
  $10/rolling-10-min, billing-tier cap $250. No 20-RPD-class daily cap; per-model RPM/TPM
  limits are shown live in AI Studio. Tier 2 ($100 cumulative spend + 3 days) raises caps
  further if ever needed.
- `ai.google.dev/gemini-api/docs/pricing` + `/models` (live), current model IDs and paid
  Tier prices per 1M tokens:

  | model id | in | out | role |
  |---|---|---|---|
  | `gemini-3.8-flash` | $0.75 ($1.50 from 2027-01-01) | $3.75 ($7.50 from 2027-01-01) | flash workhorse — upgrade of the free `gemini-2.5-flash` rung |
  | `gemini-3.5-flash-lite` | $0.30 | $2.50 | high-volume cheap calls (judges, watchdog) |
  | `gemini-3.1-pro-preview` | $2.00 | $12.00 | research tier — what Frank actually uses Gemini for |

- Research bonus: **Grounding with Google Search** is a paid-tier tool — 5,000 free
  grounded requests/month shared across Gemini 3.x, then $14/1k. That gives the fleet a
  sanctioned, native web-grounded research backend while fleet-search web is dead (wq#2595).
- LiteLLM supports this natively as the `gemini/` provider (`GEMINI_API_KEY` from AI
  Studio) — the existing `gemini-flash` entry already uses it, so the wiring is a model-id
  swap plus a new key, not a new integration.
- Cost sanity: FLEET_COST_PLAN.md:26 measured the old rung at ~1.6M in / 0.1M out tokens
  per 30d. At `gemini-3.8-flash` prices that is ~$1.6/mo; even 10x growth plus a
  `gemini-3.1-pro-preview` research load stays low single-digit $/mo, inside the Tier 1
  $250 cap with two orders of magnitude of headroom.

### (c) Antigravity CLI — consumer sibling, same disqualifier.

`cloud.google.com/blog/topics/developers-practitioners/choosing-antigravity-or-gemini-cli/`
(2026-02-04): Antigravity is the agent-manager/IDE experience, Gemini CLI the
terminal/headless one; both share the Code Assist quota pool above. Same ToS restriction,
same ban reports (the r/google_antigravity thread above IS an Antigravity ban). Not a
programmatic path at all — skip.

## Wiring (staged in this repo, hand-merge on deploy)

The live gateway config `/home/lumen/agent/litellm_config.yaml` is not version-controlled
and not reachable from a BUILD sandbox (established convention: wq#897/948/1295/1406/1590).
The merge fragment + runbook live at `ops/wq2627-gemini-litellm/`:

- `litellm_config_snippet.yaml` — upgrades `gemini-flash` to `gemini/gemini-3.8-flash`,
  adds `gemini-flash-lite` and the research-tier `gemini-pro-research`, and adds the
  missing fallback (`gemini-flash` -> `cerebras-oss-120b`) so a 429 no longer dead-ends —
  wq#2580's option (b), applicable IMMEDIATELY, before the paid key exists.
- `README.md` — deploy runbook: key mint (Frank), env var, merge, restart, verify probes.

Router registration: `research/models.yaml` now carries a `gemini-3-flash` row (pools:
[cheap_cloud], like deepseek-v4-flash) with the research-tier trust line and a DARK status
that flips to LIVE-local when the paid key lands. It is a LiteLLM research/judge backend,
not a CC worker, so it joins no fworker routing chain — same treatment as deepseek.

## The one physical act (Frank; non-blocking, queued as a wq physical task)

1. In AI Studio (aistudio.google.com): create/select the API project, **link a billing
   account** (instant Tier 1), mint an API key. Drop it 0600 into the gateway env as
   `GEMINI_API_KEY_PAID` (path-only handling; never in a remote URL or config literal).
2. Optional, for his own terminal: reply with his Google AI tier (Pro/Ultra/Plus/One) —
   Pro/Ultra get the 1500/2000-per-day Gemini CLI allowance, Plus/One do not. This affects
   nothing in the fleet wiring.

## wq#2580 disposition

Closed by this ticket: the fallback dead-end has a staged fix (snippet §fallbacks,
deployable now), and the quota decision it flagged for Frank is resolved as "enable paid
Tier 1" with the key mint queued as a physical task. Until the key lands the free rung
stays best-effort with a real fallback behind it instead of a drop.
