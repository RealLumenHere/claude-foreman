# CLAUDE-FOREMAN — BUILD PLAN (orchestrator canonical)
Owner: Frank. Author: foreman-r1, 2026-08-14. Status: RESEARCH COMPLETE → cleared to build.
This is the single map the orchestrator dispatches from. Every ticket below is filed in wq
(lane: build) with deps + gates; specs live here, tickets point back to these sections.

## 0. RESEARCH IS DONE — what backs this build
Three research docs (all under /home/lumen/foreman/research/), all claims tagged
LOCAL-VERIFIED / decompiled / WEB-dated / live-probed:
  FOREMAN_RESEARCH.md      — the 16-item knowledge base (roster, effort, permissions, hooks,
                             usage, policy, concurrency, local rung, prior art, failure modes,
                             session mechanics, calibration, archetypes, context) + PROBES/RISKS
                             + the 4-ticket cut.
  CLAUDE_CLI_INTERNALS.md  — decompiled 2.1.220: effort resolver (Uoe precedence + per-model
                             ceilings + launch-pin scope), model resolution + entitlement source,
                             refusal-fallback routing table, in-band usage frames, deterministic
                             LIMIT-vs-CRASH classifier, autocompact/hooks/permissions.
  PROBE_RESULTS.md         — T0 live results on this box (below).
  models.yaml              — capability manifest + routing table (archetype→model,effort) +
                             refusal-fallback map + effort-resolution facts + usage sources.

## 1. NO MORE RESEARCH REQUIRED. One gate remains:
P5 `claude setup-token` (ticket T2b-gate) — HELD, needs Frank present. It can rotate the
account refresh token server-side (GH #48786); the Max login is the ONLY Anthropic auth on this
box (API key DEFUSED); recovery is an interactive re-login. It gates ONLY durable multi-worker
auth (T2b). T1 / T2a / T3 / T4 proceed without it. Everything else is build-time discovery
(native-agents spike, Bes VM ingest URL) or post-build (CAL-1 calibration) — not research.

## 2. WHAT'S ALREADY TRUE (so nobody re-derives it)
- Routed roster on Max: fable-5 / opus-5 / sonnet-5 / haiku-4-5 + local rung. opus-4-8 stays
  ENTITLED (it is the cyber-refusal landing zone) but UNROUTED. Opus 3 = tombstone (shows,
  won't serve). mid-Opus/sonnet-4.6 dominated → unrouted. [PROBE_RESULTS P1/P2/backbone]
- No org model allowlist and no per-model effort caps on this account (bootstrap model_access
  null) → effort ceilings = the decompiled model defaults. [PROBE_RESULTS P3b]
- Effort: names only (low/medium/high/xhigh/max; "Extra"==xhigh). Precedence
  CLAUDE_CODE_EFFORT_LEVEL(env) > --effort/settings > model default. Pass --effort explicitly on
  fable-5/opus-4-8/opus-4-7 to clear the launch-pin. Verify actual effort via $CLAUDE_EFFORT /
  hook effort.level. [CLAUDE_CLI_INTERNALS §A; --effort max live-accepted on Fable, P2]
- Governor source = GET /api/oauth/usage (bearer + `anthropic-beta: oauth-2025-04-20`), zero
  quota; parse limits[] {kind session|weekly_all|weekly_scoped, percent, resets_at, scope}.
  Fable draws its own weekly_scoped "Fable" bucket (live-confirmed). Token expires ~8h; any
  claude call refreshes it — refresh before a raw curl. In-band alternative: rate_limit_event +
  api_error frames in stream-json. [PROBE_RESULTS P3a; CLAUDE_CLI_INTERNALS §D]
- LIMIT-vs-CRASH: error result + (rate_limit_event.rejected | api_error.rate_limits!=null |
  category rate_limit) ⇒ LIMIT (use resets_at); error_max_budget_usd / error_max_turns are
  their own subtypes; else CRASH. [CLAUDE_CLI_INTERNALS §E]
- Refusal-fallback (fixed): bio→opus-5, cyber→opus-4-8; opus-5-origin bio = hard refuse. Parse
  model_refusal_fallback / _no_fallback. [CLAUDE_CLI_INTERNALS §C]
- Local rung works end-to-end: ANTHROPIC_BASE_URL=localhost:4000 + LiteLLM key + deepseek-v4-
  flash ran a real Bash tool round-trip, zero Anthropic quota. [PROBE_RESULTS P6]
- Concurrency: 3 parallel -p, distinct cwds → distinct sessions, no collision (refresh-race not
  exercised; that's what T2b/P5 addresses). Harness must use --max-turns ≥2, stdin </dev/null.
  [PROBE_RESULTS P7-lite]
- Permissions: acceptEdits + allow/deny rules + PreToolUse guard hook. NEVER bypassPermissions.
  [FOREMAN_RESEARCH §4]

## 3. THE TICKET DAG (lane: build). model/effort = how to dispatch the BUILD ticket itself.
     T0 (research) = DONE: wq#605, wq#609, wq#611.

┌─ T1 foreman-usaged ──────────── deps: none
├─ T2a worker-harness ─────────── deps: T1
│    ├─ T2b-gate  setup-token ──── deps: T2a  (HUMAN-GATED: Frank present)
│    └─ T2b concurrency-harden ── deps: T2a, T2b-gate
├─ T3 orchestrator-tier ───────── deps: T2a
├─ T4 local-rung ──────────────── deps: T2a
└─ CAL-1 calibration ──────────── deps: T2a, T3   (SCHEDULE: after 2026-08-19, off-peak)

### T1 — foreman-usaged (budget governor daemon)
dispatch: standard-engineering → sonnet-5@high. gate: probe (unit tests). refs: PROBE_RESULTS
§P3a, FOREMAN_RESEARCH §6/§7, CLAUDE_CLI_INTERNALS §D.
Acceptance:
- Detached daemon (lumen-run or systemd unit; survives DC-gateway recycle). Polls
  GET /api/oauth/usage every 60–90s (bearer from ~/.claude/.credentials.json + header
  anthropic-beta oauth-2025-04-20; refresh-aware: if 401, trigger a claude refresh then re-read).
  Parses limits[] → session / weekly_all / weekly_scoped(Fable).
- Fallback to CCC (claude.ai /api/organizations/06598063-72d8-4013-938d-ec6a9213b3e4/usage) if
  primary fails 2×; FAIL-CLOSED to "local-rung-only" if both stale >10 min; ntfy CRITICAL.
- Reserve floors (config file, defaults): session ≥20% remaining, weekly_all ≥15% remaining,
  Fable weekly_scoped keep ≥50% of cap for Frank. Off-peak flag weekdays 05:00–11:00 PT.
- Exposes a `can_dispatch(model)` decision (file or socket or CLI) the harness queries.
- Kill-switch: /home/lumen/foreman/STOP halts all dispatch. State: /home/lumen/foreman/state.json
  atomic. ntfy on floor breach and on resets.
Verify (adversarial): floor logic unit-tested; feed a synthetic rejected rate_limit_event →
must block; touch STOP → must halt; kill token → must fail-closed to local-only.

### T2a — worker harness (single worker)
dispatch: novel-design → fable-5@high for the harness DESIGN doc, then sonnet-5@high to build.
gate: Frank reads design before build + probe. refs: FOREMAN_RESEARCH §4/§5/§12/§13,
CLAUDE_CLI_INTERNALS §A/§E/§F, PROBE_RESULTS (harness notes).
FIRST SUBTASK (≤30-min spike, no quota beyond one no-op): evaluate native `claude --bg` /
`claude agents --json` as the supervision substrate; adopt only if per-worker env + settings
injection survive the dispatch path; else build bespoke process tracking.
Acceptance:
- Spawns one `claude -p` per ticket via lumen-run: per-ticket git worktree cwd, HOME=/home/lumen,
  CLEAN env (cloud workers: unset ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL), stdin </dev/null.
- (model, effort): pass --model + --effort <name>; --max-turns per archetype (≥2); wall-clock
  timeout. Optional hard pin CLAUDE_CODE_EFFORT_LEVEL=<name> in child env.
  CLAUDE_CODE_AUTO_COMPACT_WINDOW=500000 for cloud workers.
- Settings pack via --settings worker-settings.json: mode acceptEdits; allow/deny rules (deny
  classes verbatim: reboot/shutdown, systemctl stop|disable|mask, dd, --remove-orphans,
  ip link set, macchanger, git push --force, rm -rf /, docker except ps/logs, Read of .env /
  credentials / .credentials.json). NO bypassPermissions. guard.sh PreToolUse hook =
  fail-closed regex re-implementing the deny classes + host-scope check; exit 2 blocks.
- Supervisor parses --output-format stream-json --include-hook-events: capture init(session_id);
  inactivity watchdog (kill after N min no event); subtype classifier + LIMIT-vs-CRASH per
  INTERNALS §E. Guard/Stop hook echoes $CLAUDE_EFFORT into events JSONL (effort verification).
- wq wrapper: claim --id / periodic heartbeat / done / release; on reap → explicit wq release.
  resume-after-LIMIT (--resume session_id); restart-after-CRASH (fresh worktree + prior-attempt
  notes file); attempt counter; 2 strikes → wq block for Frank with transcript path.
- OTEL → Bes VictoriaMetrics: CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_METRICS_EXPORTER=otlp,
  OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative, resource attrs {worker,ticket,
  model,effort}. (Confirm Bes VM ingest URL first — 1-line check.)
- Retrofit /home/lumen/bin/gh-lane-worker OFF bypassPermissions onto this same pack.
Verify (adversarial): run against the CAL-1 scratch fixture; a denied verb (e.g. docker compose
--remove-orphans) is blocked at the hook; a synthetic rejected-limit frame classifies LIMIT not
CRASH; worktrees isolate; $CLAUDE_EFFORT in the JSONL matches the intended effort.

### T2b-gate — setup-token (HUMAN-GATED, Frank present)
dispatch: human. refs: FOREMAN_RESEARCH §8, PROBE_RESULTS HOLD.
Acceptance:
- Back up ~/.claude/.credentials.json (timestamped) BEFORE anything.
- Run `claude setup-token` in a quiet window (zero concurrent workers).
- Immediately verify BOTH: existing login still works (auth check + one tiny -p), AND the new
  token works via CLAUDE_CODE_OAUTH_TOKEN on a tiny -p.
- If the live login broke: restore backup, re-login, flag. If good: store the sk-ant-oat token
  in Vault (or creds file 0600) as the foreman worker bearer.
Gate rationale: server-side refresh-token rotation is not backup-recoverable; re-auth is
interactive → must be attended.

### T2b — concurrency hardening
dispatch: standard-engineering → sonnet-5@high. deps: T2a, T2b-gate. refs: FOREMAN_RESEARCH §8,
PROBE_RESULTS P7-lite.
Acceptance:
- All cloud workers use CLAUDE_CODE_OAUTH_TOKEN=<setup-token> (static bearer → no refresh churn,
  kills the shared-credential race by construction). Interactive/ORCH sessions keep normal login.
- Concurrency cap enforced (start 2, ceiling 3 cloud + N local), governor-gated; stagger spawns
  2–3s.
Verify: 3 concurrent real tickets, ideally across a token-expiry boundary → no auth cascade,
all complete.

### T3 — orchestrator tier
dispatch: novel-design → fable-5@high. deps: T2a. refs: FOREMAN_RESEARCH §10/§11/§15,
models.yaml, CLAUDE_CLI_INTERNALS §C.
Acceptance:
- wq schema: additive ALTER adds `model`,`effort` columns (+ `wq edit` support). agent-prompt
  emits the (model,effort) tuple header (fable mode already exists, wq#580).
- models.yaml routing loader: archetype → (model, effort) with downgrade paths + the
  dominated_never_select guard. Effort-first downgrade doctrine.
- Fable-5@high judgment sessions on cadence + burn-down triggers (max reserved for hardest
  investigations until CAL-1 justifies more).
- Refusal awareness: keep opus-4-8 entitled; parse model_refusal_fallback / _no_fallback.
- Adversarial verify-pass dispatch: sonnet-5@high, fresh instance ≠ author model, per archetype 8.
Verify: assign 5 sample tickets across archetypes → correct (model,effort) chosen; a
dominated tuple is never selected; verify-pass fires on a shipped diff.

### T4 — local rung
dispatch: standard-engineering → sonnet-5@high. deps: T2a. refs: FOREMAN_RESEARCH §9,
PROBE_RESULTS P6.
Acceptance:
- Mint a SCOPED 'foreman' LiteLLM virtual key (NOT the master key used in the P6 probe).
- Per-worker env: ANTHROPIC_BASE_URL=http://localhost:4000, ANTHROPIC_AUTH_TOKEN=<foreman key>,
  unset ANTHROPIC_API_KEY, CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1. Never global; never
  in a shell that launches a cloud worker.
- Archetype restriction: mechanical-edit / probe-build / verify-drafts only; lower --max-turns.
- Mandatory Sonnet-5 verify-pass gate before lumen-pr land; NO direct-to-main from the rung.
- Trusted routes: deepseek-v4-flash (P6-validated), qwen36-local (gated). nephthys-qwen3-14b is
  NOT a worker (fallback brain only).
Verify: a mechanical-edit ticket runs on deepseek-v4-flash, lands only after Sonnet verify-pass.

### CAL-1 — calibration (SCHEDULE after 2026-08-19, off-peak)
dispatch: probe-build → sonnet-5@medium. deps: T2a, T3. refs: FOREMAN_RESEARCH §14, models.yaml.
Acceptance: build the CAL-1 fixture (scratch git repo + scratch wq DB + scratch board, reset
script); run the matrix SERIALLY sampling /api/oauth/usage %-delta per pool before/after each
run (+ OTEL tokens); write measured[] blocks into models.yaml and flip routing rows est→measured.
Note: numbers before 2026-08-19 are inflated by the temporary weekly-limit boost — do not
calibrate before then.

## 4. SEQUENCING
Wave 1 (parallel): T1, and T2a's design-doc subtask (Fable). Frank reads the T2a design.
Wave 2: T2a build (Sonnet) → then T3 and T4 in parallel (both dep only T2a).
Gate: T2b-gate (Frank+setup-token) whenever convenient → unblocks T2b.
Wave 3: T2b (after gate) + CAL-1 (after 2026-08-19).
Bootstrapping note: the foreman does not exist yet, so these BUILD tickets are dispatched by the
CURRENT orchestrator (Claude ORCH sessions / the interim detached-agent pattern), NOT by the
foreman. Once T1+T2a land, the foreman can start dispatching NON-foreman lanes itself.

## 5. STANDING GUARDRAILS for every foreman-build worker
Read-safe on production; back up before any config/db/container change; never bypassPermissions;
never touch DEFUSED ANTHROPIC_API_KEY; no reboot; restore any stopped service before session end
(flag if not). Land via lumen-pr (branch → PR → probe → board), never direct-to-main. GitHub
posts read human. Not done until the probe passes and the board line is written.
