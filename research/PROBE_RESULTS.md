# T0 PROBE RESULTS — live, on Frank's box (foreman-r1, 2026-08-14 PDT)
Executed against claude 2.1.220 on Lumen under Frank's Max login (org claude_max 20x). All
completion probes ran detached via lumen-run (survives DC-gateway recycle). setup-token (P5)
intentionally NOT run — see HOLD at bottom. Nothing else touched; no services stopped.

## P1 — Opus 3 serve status: TOMBSTONE (confirmed)
`claude -p --model claude-3-opus-20240229` → is_error:true, subtype "success" but result:
"There's an issue with the selected model (claude-3-opus-20240229). It may not exist or you may
not have access", modelUsage {} (nothing served), cost 0, plus stderr deprecation notice
"end-of-life January 5th, 2026". VERDICT T07: appears in the picker, does NOT serve. Unroutable.

## P2 — Fable 5 on Max + --effort max: PASS
`claude -p --model claude-fable-5 --effort max` → is_error:false, stop end_turn, served
claude-fable-5, NO credits/entitlement error. → Fable is included on Max headless AND --effort
max is accepted headless. BURN SIGNATURE: the trivial "reply OK" prompt produced 263 output
tokens at max effort (vs 4 tokens for opus-5/sonnet-5 with no effort) — the max-effort
overthinking the docs warn about, visible even on a no-op. (Metering cost $0.45 API-equiv; not
real spend on Max — the real cost is the weekly_scoped tick, below.)

## Backbone — Opus 5, Sonnet 5: PASS
Both served, is_error:false. Confirms the routing backbone is live (opus-5 is also the Max
default). Combined with P2, the routed roster fable-5/opus-5/sonnet-5/haiku-4-5 is all live.

## P3a — GET /api/oauth/usage: WORKS, and IS the governor source
Auth = OAuth bearer from ~/.claude/.credentials.json (claudeAiOauth.accessToken) +
header `anthropic-beta: oauth-2025-04-20`. HTTP 200. ZERO completion quota. NOTE: the stored
access token expires ~8h; a routine `claude` call refreshes it (the CLI rewrites
.credentials.json). A raw curl with a stale token 401s — refresh first (any claude call does it).
LIVE PAYLOAD (the shape the T1 governor parses):
  limits: [
    { kind:"session",       group:"session", percent:29, severity:"normal", resets_at:<5h>,  scope:null },
    { kind:"weekly_all",    group:"weekly",  percent:52, severity:"normal", resets_at:<7d>,  scope:null },
    { kind:"weekly_scoped", group:"weekly",  percent:57, severity:"normal", resets_at:<7d>,
      scope:{ model:{ id:null, display_name:"Fable" }, surface:null }, is_active:true } ]
  five_hour:{utilization,resets_at}  seven_day:{utilization,resets_at}
  seven_day_opus:null  seven_day_sonnet:null  seven_day_oauth_apps:null   (opus/sonnet sub-meters
    NOT enforced on this Max acct — they're null; only session + weekly_all + Fable-scoped bind)
  extra_usage:{ is_enabled:false, monthly_limit:10000, used_credits:0, utilization:0,
    disabled_reason:"out_of_credits", credits_ever_enabled:true, can_purchase_credits:false }
READINGS at test time: session 29%, weekly_all 52% (INFLATED by the temp weekly boost ending
2026-08-19), Fable weekly_scoped 57%. A single Fable@max call moved Fable-scoped 56%→57% —
LIVE PROOF Fable draws its own "Fable" weekly_scoped bucket. → Governor floors watch limits[]:
kind==weekly_scoped & scope.model.display_name=="Fable" for the Fable reserve.

## P3b — GET /api/claude_cli/bootstrap: WORKS (entitlement)
Same auth/header. HTTP 200. Returned: `model_access: null` → NO org allowlist / NO per-model
effort caps on this account ⇒ the full Max tier roster is selectable and effort ceilings are the
model defaults (the decompiled OI/eqe/I_e lists in CLAUDE_CLI_INTERNALS §A — authoritative here).
Also: oauth_account.organization_type "claude_max", rate_limit_tier "default_claude_max_20x";
`client_data.cedar_lagoon = { "claude-fable":true, "claude-mythos":true }` (Mythos flag is TRUE
on this acct — selectability unconfirmed, not routed); additional_model_options carries
`claude-fable-5[1m]` (disabled_reason null at this layer, but credits-off likely disables it at
pick time); cedar_basin "2026-08-31" = the Sonnet-5 promo end. → For roster/effort, bootstrap
confirms "no restrictions"; the effort ceilings come from code, not the server, on this acct.

## P6 — Local rung (ANTHROPIC_BASE_URL→LiteLLM): PASS end-to-end (zero Anthropic quota)
env ANTHROPIC_BASE_URL=http://localhost:4000 + ANTHROPIC_AUTH_TOKEN=<litellm key>,
`--model deepseek-v4-flash`, unset ANTHROPIC_API_KEY. → is_error:false, 2 turns, served
deepseek-v4-flash, and it ACTUALLY ran the Bash tool: result reported `rung-ok` + hostname
`lumen`. So the base-url swap + Anthropic↔provider tool-use translation + agentic loop all work
through our gateway. (Benign stderr: "connectors disabled because ANTHROPIC_API_KEY/other auth
is set" — expected when AUTH_TOKEN is set.) LiteLLM :4000 serves 31 models incl. deepseek-v4-
flash/-pro, qwen36-local, isis-qwen3-32b, nephthys-qwen3-14b. T4 note: mint a scoped 'foreman'
key instead of the master key used here.

## P7-lite — 3 parallel on the EXISTING OAuth, distinct cwds: concurrency PASS
Spawned 3 `claude -p` SIMULTANEOUSLY (no stagger), distinct cwds, shared ~/.claude. →
3 DISTINCT session_ids, all reached the model (haiku-4-5), NO credential-refresh race, NO
transcript collision. (All 3 returned error_max_turns "Reached maximum number of turns (1)"
because haiku tool-called on turn 1 with --max-turns 1 — a turn-budget artifact, NOT a
concurrency failure.) CAVEAT: the token was 8h-fresh so NO refresh fired — this proves parallel
+ cwd isolation but does NOT exercise the refresh-token race (#43392/#48786). That race only
manifests when a refresh fires mid-run; the setup-token static-bearer fix (P5) is what removes
it, and P5 was held. Harness note: --max-turns ≥2 and/or --disallowedTools for trivial calls.

## HOLD — P5 (`claude setup-token`) NOT run, on purpose
P5 mints a long-lived token but can rotate the account refresh token SERVER-SIDE (GH #48786).
Frank's Max login is the ONLY Anthropic access on this box (ANTHROPIC_API_KEY is DEFUSED); if
setup-token rotates and breaks it, recovery needs an INTERACTIVE browser/device OAuth re-login —
not automatable, and a local .credentials.json backup can't undo a server-side rotation. It is a
T2 prerequisite (durable multi-worker static bearer), NOT a research blocker — everything the
research needed is now confirmed above. Recommend running P5 only in a window where an
interactive re-auth is acceptable. Awaiting explicit "run P5".
— foreman-r1-363222
