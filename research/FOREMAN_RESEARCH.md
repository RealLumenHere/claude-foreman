# FOREMAN_RESEARCH — claude-foreman knowledge base
Agent: foreman-r1-363222 · Ticket: wq#605 · Written 2026-08-14 ~21:15 PDT (2026-08-15 04:15Z)
Local ground truth: claude CLI **2.1.220** at /home/lumen/.npm-global/bin/claude (native build
claude.exe 263MB, npm pkg mtime Jul 30 2026), auth = claude.ai OAuth, subscriptionType=max,
org 06598063-72d8-4013-938d-ec6a9213b3e4. [LOCAL-VERIFIED `claude --version`, `claude auth status`]

Tags: [LOCAL-VERIFIED <how>] = verified live on Lumen this session.
[WEB <url>, <date>, <version-it-applies-to>] = sourced. [UNRESOLVED] = honest gap → PROBES.
Strings-in-binary evidence comes from `strings claude.exe` dump at
/tmp/foreman-r1-363222/cli_strings.txt (regenerate: `strings -n6 <cli> > f`).

---
## 1. HEADLESS CLAUDE CODE SURFACE (2.1.220)
All flags below verified against local `claude --help` [LOCAL-VERIFIED] unless noted.

Core: `-p/--print` (non-interactive; workspace-trust dialog skipped; invalid settings files
silently ignored in -p — the help text itself warns about both). `--output-format text|json|
stream-json`; `--input-format text|stream-json`; `--include-partial-messages` (stream deltas);
`--include-hook-events` (hook lifecycle merged into stream-json); `--json-schema` (structured
output validation); `--replay-user-messages`; `--forward-subagent-text`.

Run bounding: `--max-turns N` · `--max-budget-usd` (-p only; caps API dollar spend — on
subscription auth cost is notional, still emitted as total_cost_usd; keep --max-turns +
wall-clock timeout as the real bounds) · `--fallback-model a,b,c` (-p only; comma list, max 3
after dedup, retries primary each user turn; fires ONLY on overload/unavailable/server errors —
NEVER on rate-limit/usage-limit/auth errors) [LOCAL-VERIFIED --help + WEB
https://code.claude.com/docs/en/model-config.md, fetched 2026-08-14, current].

Sessions: `--continue/-c`, `--resume [id]`, `--session-id <uuid>`, `--fork-session`,
`--no-session-persistence` (-p; unresumable), `--from-pr`. Model/effort: `--model <alias|full>`
(help text names 'fable','opus','sonnet' aliases and 'claude-fable-5' as full-name example),
`--effort low|medium|high|xhigh|max`. Prompting: `--system-prompt`, `--append-system-prompt`
(+ -file variants per docs). Permissions: `--permission-mode acceptEdits|auto|bypassPermissions|
manual|dontAsk|plan`, `--allowedTools/--disallowedTools`, `--tools` (restrict built-in set),
`--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions` (available-not-default).
Env/config: `--settings <file|json>`, `--setting-sources user,project,local`, `--add-dir`,
`--mcp-config`, `--strict-mcp-config`, `--agents <json>`, `--agent`, `--plugin-dir/--plugin-url`.

NEW vs what our older notes assumed [LOCAL-VERIFIED --help]:
- `--bg/--background` + `claude agents` = NATIVE background-agent manager. `claude agents
  --json` prints active sessions as JSON without a TTY (scriptable); dispatch defaults for
  model/effort/permission-mode settable on the agents view. Directly relevant to T2 (below).
- `--worktree/-w` native git-worktree-per-session; `--tmux`.
- `--bare` minimal mode — but it hard-locks auth to ANTHROPIC_API_KEY/apiKeyHelper (OAuth and
  keychain never read) → **unusable on our Max OAuth path**; it IS usable for the local rung.
- `--safe-mode` (all customizations off; auth/model/permissions normal).
- `ultrareview` cloud multi-agent review subcommand; `setup-token` (see §7/§8).

Working dir: cwd at spawn = project root; transcripts land in
~/.claude/projects/<dash-encoded-cwd>/<session>.jsonl — verified dirs exist for -home-lumen,
-home-lumen-messenger, -tmp-teamprobe etc. [LOCAL-VERIFIED ls ~/.claude/projects]. Per-worker
cwd (worktree) ⇒ per-worker transcript namespace = free isolation. ~/.claude also holds
session-env/<uuid>/ per session, sessions/, tasks/, shell-snapshots/, backups/,
.credentials.json (0600) [LOCAL-VERIFIED ls].

Exit codes: no complete table is published. Hard failures (max-turns overflow, stdin >10MB cap)
exit non-zero; a model REFUSAL is exit 0 / subtype "success" — you cannot catch refusals from
the exit code, parse the result [WEB https://hidekazu-konishi.com/entry/
claude_code_cicd_and_headless_automation.html, 2026-06-07, 2.1.x; WEB https://ocdevel.com/
podcaster/claude-code/7fdc1bc3, 2026-06-06]. Branch on zero-vs-nonzero + read structured output.
Result-event subtypes present in the 2.1.220 binary: `success`, `error_max_turns`,
`error_during_execution`, `error_max_budget` [LOCAL-VERIFIED strings]. json result fields:
type/subtype/is_error/result/session_id/total_cost_usd/duration_ms/duration_api_ms/num_turns/
modelUsage (modelUsage = actual model used; stderr model-substitution warnings are suppressed
under json/stream-json, so read modelUsage) [WEB code.claude.com/docs/en/model-config.md,
2026-08-14 + headless docs mirrors]. stream-json = NDJSON: first event system/init (session id,
model, tools, MCP servers, plugins), then user/assistant messages, api_retry events, final
result event [WEB hidekazu-konishi headless, 2026-06-07].

Recent-change watchouts: `claude --help` does NOT list every flag (docs say so explicitly)
[WEB https://code.claude.com/docs/en/cli-reference, snippet fetched 2026-08-14]. v2.1.199
changed leading `--dangerously-skip-permissions daemon <sub>` routing [same source]. /model in
-p applies session-only since ~2.1.153-era behavior split [WEB model-config.md].

---
## 2. MODEL ROSTER ON MAX (LOAD-BEARING) — intended list reconciled
Evidence classes: (a) exact strings in the 2.1.220 binary [LOCAL strings], (b) official docs
[WEB code.claude.com/docs/en/model-config.md fetched 2026-08-14; platform.claude.com models
overview snippet 2026-08-14], (c) probe (quota) still needed for final picker confirmation.

| Frank | Model      | Verdict | Exact string | Notes |
|-------|-----------|---------|--------------|-------|
| T01 | Fable 5     | LIVE    | `claude-fable-5` (+ option row `claude-fable-5[1m]`, "$10/$50 per Mtok" — cached in local .claude.json additionalModelOptionsCache) | alias `fable`; needs CC ≥2.1.170; NOT default on any tier; native 1M |
| T02 | (reserved)  | n/a     | `claude-mythos-5` exists in binary | Mythos = approved-orgs only; not ours |
| T03 | Opus 5      | LIVE    | `claude-opus-5` (78 refs; dateless) | DEFAULT model on Max since CC v2.1.219 (we run 2.1.220 ⇒ qualified); $5/$25 = same as 4.8 [WEB model-config.md + claudefa.st/blog/models/model-selection, 2026-08-14] |
| T04 | Opus 4.8    | LIVE    | `claude-opus-4-8` | ≥2.1.154; Fable's cyber-flag fallback target |
| T05 | Opus 4.7    | LIVE(-ish) | `claude-opus-4-7` | in binary + docs alias-history + effort table; xhigh was its default; **picker selectability on Max = PROBE P1** |
| T06 | Opus 4.6    | LIVE(-ish) | `claude-opus-4-6`, dated `claude-opus-4-6-20251101` | effort caps at high/max-no-xhigh; PROBE P1 |
| T07 | Opus 3.0    | IN-PICKER (serve=P1) | docs table in binary: `claude-3-opus-20240229` dated Jan 5, 2026 | CORRECTED 2026-08-14: "Opus 3" listed in Frank's Max picker (screenshot) under More models. Retire-date vs picker-presence conflict — whether selection SERVES = PROBE P1, now standard |
| T08 | Sonnet 5    | LIVE    | `claude-sonnet-5` | ≥2.1.197; native 1M (no [1m] variant); intro $2/$10 → $3/$15 after 2026-08-31 [WEB claudefa.st 2026-08-14] |
| T09 | Sonnet 4.6  | LIVE    | `claude-sonnet-4-6`, dated `-20251114` | 1M via credits only on subs |
| T10 | Haiku 4.5   | LIVE    | `claude-haiku-4-5`, dated `-20251001` | 200K ctx; **no effort support**; Haiku "always available, can't be org-disabled" |

Aliases on Anthropic API path: `default` (acct default; = Opus 5 on Max), `best` (= Fable 5
where available else latest Opus), `fable`, `opus`→Opus 5, `sonnet`→Sonnet 5, `haiku`,
`sonnet[1m]`, `opus[1m]`, `opusplan` (Opus in plan mode → Sonnet execute) [WEB model-config.md].
Pin exact versions with full names or ANTHROPIC_DEFAULT_{FABLE,OPUS,SONNET,HAIKU}_MODEL
(ANTHROPIC_SMALL_FAST_MODEL deprecated → DEFAULT_HAIKU). All env names present in binary
[LOCAL strings].

Fable-on-Max billing (turbulent history, current state): since **2026-07-20 Fable 5 is a
standard part of Max — included at no extra cost up to 50% of weekly usage limits**, drawing
the same weekly bucket ("Fable 5 limit" meter) [WEB Help-Center "Claude Fable 5 on your plan"
as quoted in github.com/anthropics/claude-code/issues/79337, 2026-07-20; WEB claudefa.st/blog/
guide/development/fable-5-usage-credits, 2026-08-09]. June: export-control suspension 06-12 →
restored 07-01; brief credits-only window 07-08..07-19. On Pro/standard seats Fable = usage
credits at API rates. KNOWN BUG #79337: stale entitlement makes CLI wrongly demand credits /
silently downgrade to Opus 4.8 on Max — **restart Claude Code to reload entitlement**. Our
account: extra-usage credits are OFF (cachedExtraUsageDisabledReason="out_of_credits"
[LOCAL .claude.json]) — fine for included-Fable, but the [1m]-beyond-entitlement and
Sonnet4.6[1m] paths are closed.

In -p mode there is NO consent prompt: if a Fable request would bill usage credits, Claude
Code bills without asking (or, credits-off like us, the request fails) [WEB model-config.md
§"Fable 5 and usage credits"]. Foreman must therefore treat "requires usage credits" errors as
entitlement-stale (restart worker) not as budget-exhausted.

Drawdown & multipliers: Anthropic publishes NO per-model multiplier. Verified signals:
in-app UI text "Fable 5 — Uses your limits ~2x faster than Opus" [WEB developersdigest.tech
claude-usage-limits-fable-5-explained, 2026-06-10, quoting in-app UI 2026-06-09]; Anthropic
states Fable "uses limits faster", no formula [WEB future-stack-reviews.com fable-tierc,
2026-07-24]. Opus consumes faster than Sonnet (historical support-doc language). The CLI's own
runway nudges rank the levers: "try /effort medium" then "/model opus|sonnet … more runway"
[LOCAL strings ~line 264649] — i.e. EFFORT is the first-class burn knob, then model.
TEMPORARY: a Claude Code weekly-limit increase is running **until 2026-08-19** [WEB
future-stack-reviews 2026-07-24] — calibration numbers gathered this week will overstate
steady-state headroom.

Which pool each model draws [LOCAL strings + LOCAL CCC memory]: rateLimitType vocabulary in
binary = five_hour, seven_day, seven_day_opus, seven_day_sonnet, seven_day_overage_included,
seven_day_oauth_apps, + weekly_scoped; claude.ai limits[] uses kind = session | weekly_all |
weekly_scoped with scope.model.display_name (Fable matches /fable|mythos/). Working model:
every request burns five_hour + seven_day (weekly_all); Fable additionally burns its
weekly_scoped "Fable 5 limit" capped at 50% of weekly; opus/sonnet family meters exist in the
header vocabulary (seven_day_opus/seven_day_sonnet) — whether they're enforced on Max today =
[UNRESOLVED → P3 pins the live payload].

---
## 3. EFFORT CONTROL, HEADLESS (LOAD-BEARING)
Frank's 1..5 scale maps: 1=low, 2=medium, 3=high, 4=**Extra** (UI label) = `xhigh` (string),
5=max. CORRECTED 2026-08-14 post-ship: Frank's Max-session picker screenshot shows
Low / Medium / High[Default] / Extra / Max(i) — "Extra" IS the UI name; the scriptable
CLI/API string remains `xhigh`. The strings dump is blind here (native build: even a -n5
pass finds no standalone "Extra"/"Low"/"High" labels) — absence-in-strings was misread as
absence-in-UI.
[LOCAL-VERIFIED `--effort <level> (low, medium, high, xhigh, max)` in --help + 48-83 `xhigh`
occurrences in binary strings.]

Support matrix [WEB code.claude.com/docs/en/model-config.md, fetched 2026-08-14]:
- Fable 5, Opus 5, Sonnet 5, Opus 4.8, Opus 4.7 → low/medium/high/xhigh/max
- Opus 4.6, Sonnet 4.6 → low/medium/high/max (xhigh silently runs as high — unsupported levels
  fall back to highest supported at-or-below)
- Haiku 4.5 → none (launch it with no --effort)
Default = **high** on every effort-capable model except Opus 4.7 (xhigh). Effort scale is
calibrated PER MODEL — "high" on Sonnet ≠ "high" on Fable.

Per-worker mechanics for the foreman (this is the answer):
1) `claude -p ... --model <m> --effort <level>` — per-invocation, supports max, and is the ONLY
   reliable way to break the "model-default hold": first-ever run of Fable5/Opus4.8/4.7 pins
   that model's default effort across sessions until an explicit choice; a `/effort` inside -p
   reports "Not applied" while the hold is in force. Opus 5 has no hold. [WEB model-config.md]
2) Env `CLAUDE_CODE_EFFORT_LEVEL=<level|auto>` — highest precedence of all methods, per-process
   settable, accepts max; [LOCAL-VERIFIED string present in 2.1.220]. Cleanest for spawned
   workers: foreman sets it in the child env alongside the model.
3) settings `effortLevel` — persisted default; accepts low/medium/high/xhigh only (no max), and
   community-reported settings "upgrades" have silently stripped the key [WEB docs.bswen.com
   2026-03-13] → don't build on it.
Precedence: env var > configured level > model default; skill/subagent frontmatter `effort:`
overrides session level (not the env var) — useful later for per-phase effort inside one worker.

Extras: `ultracode` = session-only Claude Code mode (xhigh + dynamic workflow orchestration),
`--effort ultracode` needs ≥2.1.203; `ultrathink` keyword in a prompt = one-turn deeper
reasoning, API effort unchanged. Adaptive reasoning is ALWAYS ON for Fable 5 / Sonnet 5 /
Opus 4.7+ (MAX_THINKING_TOKENS + CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING only apply to the
4.6 pair); **thinking cannot be turned off on Fable 5** — effort is its only throttle.
[WEB model-config.md]

Does effort change burn? YES — directionally verified three ways: docs ("lower effort is
faster and cheaper"; max "diminishing returns, prone to overthinking — test before adopting"),
the CLI's own limit-warning suggests `/effort medium` for runway [LOCAL strings], and field
reports of Fable@max draining a 5h window in minutes [WEB developersdigest 2026-06-10].
Exact Fable5@max vs @high ratio is unpublished → measured by item 14. `unpinFable5LaunchEffort`
exists in binary (launch-effort pinning artifact) [LOCAL strings].
Org effort caps exist (Enterprise roles; ≥2.1.195) — n/a for us but explains any future clamp
warnings [WEB model-config.md].

---
## 4. PERMISSIONS FOR UNATTENDED WORKERS
Mechanisms in 2.1.220 [LOCAL --help + WEB code.claude.com settings/hooks docs, 2026-08]:
permission modes (manual default, acceptEdits, dontAsk, auto = Sonnet-5-classifier-driven,
plan, bypassPermissions), settings `permissions: {allow:[], deny:[], ask:[]}` rule strings
like `Bash(git *)`, `Read(...)`, tool filters (--allowedTools/--disallowedTools/--tools), and
hooks (PreToolUse exit-2 blocks — the only layer that cannot be prompt-engineered around;
permission Bash-rules are PREFIX matchers and are evadable via quoting/`bash -c` tricks, which
is why the guard hook is mandatory, not optional).

CURRENT DEBT: /home/lumen/bin/gh-lane-worker runs `claude -p --permission-mode
bypassPermissions` [LOCAL-VERIFIED read]. That was acceptable for the github lane's narrow
prompt; the foreman generalizes it and MUST NOT inherit it.

Recommended worker pack (settings file passed via `--settings /home/lumen/foreman/
worker-settings.json`, mode acceptEdits, NO bypass):
- allow: Read/Grep/Glob everywhere in worktree; Edit/Write scoped to worktree +
  /home/lumen/agents/work-queue/reports/ + boards; Bash allow-prefixes: git (no push --force),
  /home/lumen/bin/wq*, /home/lumen/bin/lumen-pr*, /home/lumen/bin/slock*, python3, pytest,
  node, npm test, sqlite3 (ro URIs), curl to localhost ntfy, lumen_episode/lumen_state.
- deny (house rules, verbatim classes): `Bash(*reboot*)`, `Bash(shutdown*)`,
  `Bash(systemctl stop*|disable*|mask*)`, `Bash(dd *)`, `Bash(*--remove-orphans*)`,
  `Bash(ip link set*)`, `Bash(macchanger*)`, `Bash(git push --force*)`, `Bash(rm -rf /*)`,
  `Read(**/.env)`, `Read(**/claude_credentials.md)`, `Read(**/.credentials.json)`,
  `Bash(docker *)` except `Bash(docker ps*)`/`Bash(docker logs*)`.
- ask-tier is useless unattended (blocks forever in -p → tool call auto-denied); anything that
  would be "ask" goes to deny + wq block for Frank.
- PreToolUse guard hook (command type) = fail-closed regex on tool_input.command/file_path
  re-implementing the deny list + `[ "$(hostname)" = lumen ]`-style scope checks; exit 2 blocks
  and the stderr reason is fed back to the model [WEB pushary.com hooks-explained, 2026-07;
  code.claude.com/docs/en/hooks, fetched 2026-08-14]. Hook script lives at
  /home/lumen/foreman/hooks/guard.sh, referenced from the worker settings' hooks block.
- Do NOT grant sudo paths at all; workers run as lumen but every privileged verb is denied.
  (Optional hardening ticket later: dedicated `fworker` POSIX user without sudo.)
Net effect: full wq → branch → PR (lumen-pr) → probe → board flow runs unattended; every
house-rule-forbidden verb is dead at three layers (deny rule, guard hook, absent sudo grant).

---
## 5. LIFECYCLE HOOKS + MONITORING
Hooks: 30-31 events as of Aug 2026 (SessionStart/SessionEnd, UserPromptSubmit, PreToolUse/
PostToolUse/PostToolUseFailure/PostToolBatch, Stop/StopFailure/SubagentStart/SubagentStop,
PreCompact/PostCompact, Notification, ConfigChange, FileChanged, CwdChanged, TaskCreated/
TaskCompleted, TeammateIdle, WorktreeCreate/Remove, InstructionsLoaded, …). Two handler types:
`command` (JSON on stdin; exit 0 allow / exit 2 block-where-blockable, stderr → model; stdout
JSON for fine-grained decisions) and `http` (same JSON as POST body; ≥4th handler type per
2.1.x changelog). Common envelope: session_id, transcript_path, cwd, hook_event_name,
permission_mode. [WEB code.claude.com/docs/en/hooks fetched 2026-08-14; blakecrosley.com
hooks-explained 2026-07-01 ("31 events as of Aug 8 2026" per its update); pushary.com 2026-07.]

Foreman event feed, two channels:
1) PRIMARY: parse worker stdout stream-json (`--output-format stream-json --verbose` +
   `--include-hook-events` to interleave hook lifecycle events; `--include-partial-messages`
   optional) [LOCAL-VERIFIED flags]. Gives init (session_id/model/tools), every message,
   api_retry, final result subtype — enough for stuck-detection (no output N minutes),
   turn-counting, and limit/crash classification.
2) SECONDARY (survives foreman restarts, works mid-turn): per-worker hook JSONL — worker
   settings hooks for SessionStart/PreToolUse/PostToolUse/Stop/SessionEnd append one line to
   /home/lumen/foreman/events/$FOREMAN_WORKER.jsonl (worker id via env the foreman sets;
   hook commands inherit worker env).

OTEL → VictoriaMetrics on Bes: Claude Code has native OTel (beta) — set
CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_METRICS_EXPORTER=otlp, OTEL_LOGS_EXPORTER=otlp (optional),
OTEL_EXPORTER_OTLP_PROTOCOL, OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_METRIC_EXPORT_INTERVAL
[LOCAL-VERIFIED env names in binary; WEB docs.anthropic.com monitoring-usage (mirror), current;
signoz.io/docs/claude-code-monitoring 2026-08]. Metrics include session count, token usage by
type (input/output/cache-create/cache-read), cost, LoC, commits, tool decisions.
VictoriaMetrics specifics [WEB tcude.net how-i-monitor…victoriametrics, 2026-02-15]:
- MUST set `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative` — VM silently drops
  delta-temporality series.
- Enable VM's Prometheus-naming conversion so PromQL sees claude_code_token_usage_tokens_total.
- Ingest endpoint = VM's /opentelemetry/v1/metrics (http/protobuf is the friction-free proto).
Bes runs the fleet VictoriaMetrics + Grafana 13.1.1 (wq#384 fleet-inventory dashboard, vm
datasource) [LOCAL handoff/state]. Exact VM ingest URL/port on Bes: confirm on Bes before T2
wires it (1-line check, no quota) — [UNRESOLVED-minor].
Attribution: OTEL_RESOURCE_ATTRIBUTES per worker (worker id, ticket id, model, effort) so
per-run cost/token panels group correctly.

ccusage-class local parsers: read ~/.claude/projects JSONL transcripts and reconstruct
token/cost usage; useful offline cross-check, but window-alignment to Anthropic's server-side
meters is approximate — good for per-run deltas, not for "am I about to hit the wall" [WEB
ecosystem, e.g. amux.io/guides/claude-code-headless 2026-07 recommends transcript parsing for
session aggregation]. Verdict: OTEL = per-run truth for tokens; §6 endpoint = the wall.

---
## 6. USAGE/LIMITS VISIBILITY WITHOUT THE BROWSER
Three sources, ranked:
1) **PRIMARY: GET https://api.anthropic.com/api/oauth/usage** — this is what the CLI's own
   /usage screen calls ("fetchUtilization: GET /api/oauth/usage") [LOCAL-VERIFIED strings].
   Auth = the same OAuth bearer the CLI holds (~/.claude/.credentials.json, 0600). Payload
   vocabulary (from binary): five_hour, seven_day, seven_day_opus, seven_day_sonnet,
   seven_day_overage_included, seven_day_oauth_apps, utilization, resets_at, plus overage/
   grace blocks. Zero quota cost (it's a read, not a completion). Exact JSON shape + required
   headers (likely an anthropic-beta oauth header) → **PROBE P3** pins it.
2) **FALLBACK (already proven): CCC path** — claude.ai `GET /api/organizations/<org>/usage`
   (cookie auth, Thor poller). Authoritative block = limits[]:
   {kind: session|weekly_all|weekly_scoped, percent 0..100, resets_at, severity,
   scope.model.display_name} — Fable bar = weekly_scoped where display_name ~ /fable|mythos/;
   top-level five_hour/seven_day as fallback [LOCAL-VERIFIED claude_memory.md CCC section,
   bound 2026-07-08]. Org id for us: 06598063-72d8-4013-938d-ec6a9213b3e4 [LOCAL auth status].
3) Estimation (OTEL/ccusage token counts) — trend only, never a gate.
Also available free mid-run: `anthropic-ratelimit-unified-*` RESPONSE HEADERS on every API call
(status allowed|allowed_warning|rejected, -reset, -utilization, grace-5h/7d) [LOCAL strings] —
workers see the wall coming header-by-header; surfaced via OTEL logs or a PostToolUse hook is
future work, noted not planned.
FAIL-CLOSED RULE (recommendation): if the primary poll fails twice and CCC is stale >10 min,
foreman treats session+weekly as EXHAUSTED for all cloud tiers — dispatch only the local rung,
alert ntfy. Never guess remaining budget.

---
## 7. AUTOMATION POLICY (the honest boundary)
- **Feb 19, 2026**: Anthropic published/updated Claude Code Legal & Compliance documentation
  ("Authentication and Credential Use"). Core: subscription OAuth (Free/Pro/Max) is scoped to
  "ordinary use of Claude Code and other native Anthropic applications"; using those OAuth
  tokens in ANY other product/tool/service violates the Consumer ToS; the **Agent SDK
  explicitly requires API keys — subscription OAuth is not permitted with the SDK**; docs note
  subscription limits assume "ordinary, individual usage". [WEB autonomee.ai/blog/
  claude-code-terms-of-service-explained, updated 2026-02-20; corroborated WEB
  lalatenduswain.medium.com, 2026-04-10; openclawlaunch.com 2026-06-16 quoting the policy.]
- Claude Code CLI itself is Anthropic's official product "built for scripted and automated
  use"; the Consumer ToS exempts it from the automated-access prohibition — running claude -p
  under your own account on your own machine is the sanctioned shape [WEB autonomee.ai,
  2026-02/03]. Multi-model routing alongside local models is explicitly fine; the only output
  restriction is not training competing models [same].
- `claude setup-token` = the OFFICIAL long-lived credential for non-interactive use: "Set up a
  long-lived authentication token (requires Claude subscription)" [LOCAL-VERIFIED --help];
  emits sk-ant-oat01-…, ~1 year validity, consumed via CLAUDE_CODE_OAUTH_TOKEN
  [WEB gist coenjacobs headless-VPS guide + claude-code-action setup docs, 2026].
- Enforcement reality: weekly limits (added Aug 2025) exist precisely to bound continuous
  background use; **peak-hours change 2026-03-26**: weekdays 05:00–11:00 PT the 5h-session
  budget burns faster on free/Pro/Max (weekly unchanged, ~7% of users affected) [WEB
  techradar.com quoting Anthropic's Thariq Shihipar on X, 2026-03-26]. Sessions guidance:
  ~50 sessions/month "most users won't approach" [WEB support.anthropic.com/articles/11014257
  About Claude's Max Plan Usage, updated 2026-08].
- What the foreman must therefore encode: (1) OAuth/token used ONLY by the claude CLI —
  never LiteLLM-forwarded upstream, never the Agent SDK, never third-party tools; (2) modest
  concurrency (≤3 cloud workers) + waking-hours/off-peak scheduling so the pattern stays
  "an individual's heavy Claude Code use", which the plan tiers exist to sell; (3) limits are
  the contract — hitting them and waiting is compliant, engineering around them is not.
  Reselling/sharing the account: prohibited (Consumer ToS) — n/a, single-operator fleet.

---
## 8. CONCURRENT WORKERS, ONE ACCOUNT
Known failure (3 independent GH issues): concurrent sessions share
~/.claude/.credentials.json with NO file locking; on access-token expiry (~8h) all sessions
race to refresh; server rotates refresh tokens; losers 401 → cascading "Not logged in /
Please run /login" killing every parallel worker. [WEB github.com/anthropics/claude-code
issues #43392 (2026-04-04), #48786 (2026-04-15), #56339 (2026-05-05).] Status of a built-in
refresh mutex in 2.1.220: [UNRESOLVED] — assume unfixed.
Isolation lever: `CLAUDE_CONFIG_DIR` (official, present in binary [LOCAL]) points a process at
a different config dir — designed for multi-ACCOUNT; per-worker config dirs with copied
credentials would each refresh independently and make the race WORSE. Don't.

RECOMMENDED LAUNCH PATTERN (concurrency-safe):
1. Mint ONE long-lived token: `claude setup-token` (PROBE P5 first — issue #48786 reported a
   period where setup-token 401'd AND that minting could revoke the live refresh token
   server-side; run it in a quiet window and verify both the new token and the existing login
   still work). Store via Vault/creds file 0600.
2. Every worker gets env `CLAUDE_CODE_OAUTH_TOKEN=<token>` → static bearer, no refresh cycle,
   race eliminated by construction. Interactive/ORCH sessions keep the normal login.
3. Per-worker cwd = dedicated git worktree ⇒ transcript namespaces never collide
   (~/.claude/projects/<cwd>); session-env/tasks are per-session UUIDs already [LOCAL ls].
4. Stagger spawns 2-3s (defensive against any shared-file writes at startup: .claude.json
   caches, history).
5. Rate limiting is account-global: 429s and `anthropic-ratelimit-unified-*` rejected states
   hit all workers together; CLI auto-retries with backoff and emits api_retry events in
   stream-json [WEB hidekazu headless 2026-06]. --fallback-model does NOT fire on rate limits
   (§1) — foreman, not the CLI, decides what to do at the wall.
Concurrency cap: start 2, ceiling 3 cloud workers + N local-rung workers (local unlimited by
Anthropic, bounded by Lumen CPU).

---
## 9. LOCAL RUNG: ANTHROPIC_BASE_URL → LITELLM
YES — first-class, officially documented on both sides. Claude Code: ANTHROPIC_BASE_URL
"changes where requests are sent, not which model answers"; full LLM-gateway doc set exists
(llm-gateway, gateway protocol reference, per-surface config) [WEB code.claude.com/docs/en/
llm-gateway, 2026-07-04 + model-config.md]. LiteLLM: official tutorial "Use Claude Code with
Non-Anthropic Models" — point ANTHROPIC_BASE_URL at the proxy's Anthropic-Messages-format
/v1/messages; LiteLLM translates to any provider [WEB docs.litellm.ai/docs/tutorials/
claude_non_anthropic_models, current]. Env auth var = ANTHROPIC_AUTH_TOKEN (present in binary
[LOCAL]) carrying the LiteLLM virtual key.

Wiring for our fleet (per-worker env ONLY — never global, never near the Max workers):
  ANTHROPIC_BASE_URL=http://localhost:4000
  ANTHROPIC_AUTH_TOKEN=<litellm virtual key>   (mint a 'foreman' key; NOT the master key)
  CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1  (≥2.1.129; populates /model from GET
    /v1/models, rows labeled "From gateway") [WEB litellm tutorial; LOCAL env string present]
  claude -p ... --model <litellm-alias>   (gateway path skips model-name validation — any
    string passes through [WEB model-config.md])
House-rule compliance: ANTHROPIC_API_KEY in /home/lumen/agent/.env stays DEFUSED — the local
rung uses a LiteLLM key against localhost, which is not Anthropic spend. Never set
ANTHROPIC_BASE_URL in a shell that later launches a Max worker (a stale/expired OAuth-vs-
gateway-token precedence footgun is documented in the wild [WEB docs.claude-mem.ai
litellm-gateway, 2026-05]). `--bare` is usable here (locks to key auth) for deterministic runs.

Known translation quirks to expect: Claude Code attaches anthropic-beta headers on every
request (e.g. prompt-caching-scope-2026-01-05, advanced-tool-use-2025-11-20) — LiteLLM strips/
re-implements these progressively; non-Anthropic backends can choke on cache_control blocks,
interleaved-thinking expectations, and strict tool_use id round-tripping [WEB docs.litellm.ai
claude_responses_api, current]. Weak models also drown in Claude Code's large system prompt +
tool schema. claude-code-router-class shims exist as an alternative translation layer; since
LiteLLM's official path covers us and is already our fleet gateway, prefer LiteLLM —
[UNRESOLVED: router-shim maintenance survey skipped as moot].

Which of OUR routes to trust with wq tickets (rungs on litellm :4000, current config):
- deepseek-v4 / deepseek-v4-flash — best local-ish tool-caller we run; thinking-mode already
  pinned per-alias (messenger-chat off, messenger-re on) [LOCAL state]. Trust: mechanical-edit,
  probe-build, verify-pass drafts. ALWAYS review-gated before land.
- qwen36-local (Qwen3.6-35B-A3B, Isis-backed) — offline-survival rung; tool use serviceable,
  slower. Trust: mechanical-edit only, review-gated.
- nephthys-qwen3-14b — too weak for multi-step tool use; NOT a wq worker; leave as fallback
  brain only.
Review gate for the rung: every local-rung ticket lands as PR only; a Sonnet-5 verify-pass
worker (cheap) must approve the diff against the ticket before lumen-pr land. No local-rung
direct-to-main, ever. Also cap local workers' --max-turns lower (they loop more).

---
## 12. FAILURE MODES + RECOVERY (mapped onto existing plumbing)
What we already have [LOCAL: wq schema + gh-lane-worker + handoff]:
- wq: atomic claims (BEGIN IMMEDIATE), provisional 60m lease auto-promoting to 240m on any wq
  call, lease_expires/lease_ttl columns, heartbeat, release, retire (wq#568 STOP MEANS STOP),
  claim --id (wq#582), subsystem slocks with expiry, agent_sessions/agent_activity audit.
  Expired lease ⇒ ticket returns to queue "loudly" — the reaper exists.
- lumen-run: detached long jobs immune to MCP-gateway reaping — REQUIRED launcher for the
  foreman daemon itself and available for workers.
- slock: cross-lane advisory locks; ntfy for alerts; aq for triage.
Failure → handling:
- Headless hang / silent worker: foreman watchdog on stream-json inactivity (no event N min,
  default 10) + wall-clock cap (gh-lane-worker uses `timeout 3600`; keep per-archetype caps).
  Kill → wq release (or let lease lapse) → requeue with attempt counter; 2 strikes ⇒ wq block
  for Frank with the transcript path.
- Crash w/ partial work: worktree is the blast radius; foreman archives the worktree diff to
  the ticket dir, releases claim. Resume-vs-restart: RESUME (`--resume <session-id>`, id
  captured from init event) only when (a) same ticket, (b) worktree still clean-consistent,
  (c) failure was limit/transient — otherwise fresh session, fresh worktree, prompt includes
  "prior attempt notes" file. Refusals (exit 0, prose decline) count as attempts.
- Token expiry mid-run: eliminated by setup-token static bearer (§8); if the token itself
  expires (1y), everything 401s at once → foreman detects auth-class error text, pauses all
  cloud lanes, ntfy CRITICAL.
- Context exhaustion mid-ticket: §16.
- Who releases a dead worker's claim: lease expiry does (verified semantics in claim banner
  [LOCAL]); foreman additionally does explicit `wq release` on reaped PIDs so the queue never
  waits out a lease.
- Foreman itself dies: workers are independent processes (lumen-run) and finish their ticket;
  on restart foreman reconciles from `claude agents --json` + wq claimed-by-foreman-ids +
  events JSONL. State file: /home/lumen/foreman/state.json, rewritten atomically.

---
## 13. SESSION WINDOW MECHANICS UNDER CONCURRENCY
- A "session" (5h window) starts with your FIRST MESSAGE to Claude and lasts 5 hours; it is an
  ACCOUNT-level rolling window, shared across claude.ai app and Claude Code — not per-process.
  [WEB support.anthropic.com/articles/11014257 About Claude's Max Plan Usage, updated 2026-08;
  WEB dev.to/portkey claude-code-limits ("all Claude.ai plans share a common usage bucket
  across Claude app & Claude Code"), 2025-07-29 — still the operative model.]
  ⇒ N concurrent headless workers share ONE window and ONE budget; the first worker's first
  message opens the window if none is open. Weekly limits sit on top (weekly_all + Fable's
  weekly_scoped ≤50%). Peak-hours: weekday 05:00–11:00 PT sessions burn faster (§7).
- Mid-ticket limit hit: the CLI stops and waits for a human in interactive mode; in -p the run
  ENDS — "Claude Code stops mid-task"; community tooling (claude-auto-retry etc.) exists solely
  to type "continue" after the reset [WEB cheapestinference.com claude-code-usage-limit-
  auto-retry, 2026-07/08]. Binary carries the user-facing strings ("You've hit your …",
  "usage limit", resetsAt, "Your … limit resets …", allowed_warning/rejected states)
  [LOCAL strings]. What -p emits EXACTLY on a limit hit (exit code; result subtype — likely
  error_during_execution with limit text in result/is_error, vs a distinct shape; whether
  stderr carries the reset time in text mode): **[UNRESOLVED → PROBE P4]**, capture
  opportunistically at the next ≥95% weekly moment rather than burning budget to force it.
  Until pinned: foreman classifies nonzero-exit + (usage poll ≥100% on any pool OR limit
  strings in output) as LIMIT; anything else as CRASH. Fail-closed: LIMIT pauses the whole
  cloud tier until resets_at from §6, not just the one worker.
- Server-side signals exist BEFORE the wall: anthropic-ratelimit-unified-status flips
  allowed→allowed_warning→rejected with -reset epoch headers [LOCAL strings]; §6 polling at
  60-90s is sufficient granularity for a ≤3-worker fleet.
- `--resume` across a reset: sessions persist on disk (default), resume restores transcript
  and the SAVED MODEL (resume keeps the transcript's model regardless of current default
  [WEB model-config.md]) — viable to finish a ticket after reset without restarting it;
  auto-retry tooling does exactly this in the wild. Caveats: resumed context may immediately
  auto-compact (§16), and if the saved model was retired/excluded it falls through to normal
  precedence. Foreman policy: resume after LIMIT, restart after CRASH.

---
## 14. CALIBRATION PROTOCOL (design only — nothing run)
Benchmark ticket "CAL-1" (fixed, self-scoring, exercises the real flow):
- Fixture: /home/lumen/foreman/cal/ = tiny git repo (one Python module + failing pytest), a
  SCRATCH wq DB (copy of queue.db schema, never the live one) pre-seeded with one ticket, a
  scratch board file. Reset script re-creates all three (idempotent).
- Task spec (identical prompt per run): register/claim from the scratch DB via
  WQ_DB=<scratch> wrapper, read ticket, fix the module so pytest passes, run probe
  (pytest -q; exit 0 = pass), append one closeout line to the scratch board, wq done.
- Objective score: probe pass/fail (binary) + rubric: diff minimality (changed-lines ≤ N),
  board line present, done-notes format, no out-of-scope file touched (git status clean
  outside target). Score = pass AND all rubric bits.
- Record per run: wall time; num_turns + total_cost_usd + modelUsage (from -p json result);
  usage-% delta per pool (§6 endpoint sampled immediately before/after — NOTE serial runs
  only, one at a time, or deltas are unattributable); tokens by type (OTEL, resource-attr
  tagged run id); exit code; result subtype.
- Matrix (3 runs each, medians): TEST FIRST — fable-5@{high,max}, opus-5@{medium,high,xhigh},
  sonnet-5@{low,medium,high,max}, haiku-4-5@(none), deepseek-v4-flash@local,
  qwen36-local@local. SKIP as predictably dominated (§10 logic): fable-5@{low,medium}
  (judgment tier misused), opus-5@{low} vs sonnet-5@{high} (test the SONNET side instead),
  all opus-4.x and sonnet-4.6 tuples (same-or-worse than opus-5/sonnet-5 at same subscription
  burn class — only revisit if P1 shows opus-5 gated), xhigh-vs-max on sonnet for CAL-1
  (task too small to separate; defer to a CAL-2 RCA-shaped fixture).
- Timing caveat: run AFTER 2026-08-19 (temporary weekly-limit boost ends §2) and OFF-PEAK
  (§7), or the burn numbers are non-representative.
- Output: append measured burn-%/turns/wall to models.yaml `measured:` blocks; rankings in
  models.yaml switch from `est` to `measured` per tuple; low-confidence archetype rows in §15
  get resolved by a CAL-2 fixture per archetype (build only the RCA one first).

---
## 15. TICKET ARCHETYPES FROM LOCAL HISTORY (read-only mining)
Source: queue.db (ro) — 702 tickets, 521 done; lanes auto 136 / research 128 / build 104 /
user 76 / pokemon 61 / fable 40 / fleet 20 / memory 19 / github 17 / t1 1 [LOCAL sqlite].
Failure ledger: /home/lumen/agents/failure-queue (fq) exists; wq#590 [mv4-FA] is the dedicated
failure-archaeology ticket — its output should back-fill the per-archetype failure columns
below (deliberately not duplicated here). [LOCAL ls + queue titles]

Archetype → identifying signals → examples → assignment (LC = low-confidence, calibrate):
1. mechanical-edit — verbs wire/relabel/generate-from-source, single-file scope, no unknowns.
   Ex: #565 (wire standing checks to timers), #561 (six dispatch files from one source), #599
   (plug label relabel). → sonnet-5@low, downgrade local(deepseek)@—, review-gate: rung only.
2. probe-build — "probe", "CI", "measure" in title; deliverable is a test not a change.
   Ex: #588 (probe/CI hygiene), #596 (PSI measure), probe_wq582 lineage. → sonnet-5@medium,
   downgrade haiku-4-5 (LC), rung allowed w/ gate.
3. standard-engineering — feature/fix in one subsystem, pattern exists in repo.
   Ex: #582 (claim --id), #563 (lumen-pr conflict guard), #592 (power-dash → VM rollups).
   → sonnet-5@high, downgrade sonnet-5@medium, escalate opus-5@medium on 1st fail. Gate: probe
   required (house standard), no human gate.
4. RCA-investigation — "audit", "why", "bypasses", incident language; unknown root cause.
   Ex: #601/#564 (Mt Moon escape — BFS-not-vibes), #594 (dup session id), #562 (lpp-watchdog
   bypass). → opus-5@xhigh, downgrade opus-5@high, escalate fable-5@high when 2 attempts fail
   or blast radius crosses subsystems. Gate: verify-pass on the fix.
5. novel-design — new service/subsystem, interface decisions.
   Ex: #575 (lumen-memoryd), #574 (rerank wiring), #591 (plug-awareness). → fable-5@high for
   the DESIGN deliverable, execution split to standard-engineering tickets. Gate: Frank reads
   design before build (matches existing mv4 pattern).
6. long-horizon-campaign — multi-session, checkpointed (pokemon chain, simpill uplift, #567
   pivot). → orchestrator decomposes; workers get standard/RCA tuples; NEVER dispatch the
   campaign itself as one worker (context §16).
7. research-synthesis — web+local evidence, report deliverable. Ex: #569/#570/#571, #602,
   #605 (this ticket). → opus-5@high, downgrade sonnet-5@high (LC); fable-5@high only when the
   report gates a build phase. Gate: none (reports are inherently reviewed).
8. verify-pass — adversarial re-check of shipped work. Ex: #581 (fresh-eyes audit), #572/#595
   (eval gates). → sonnet-5@high (fresh instance ≠ author model where possible), escalate
   opus-5 when it contradicts the author. Gate: is the gate.
Rubric mechanics: archetype guessed from title/detail keywords + lane (pokemon→4/6 heavy,
memory→5/7, fleet→1/3); orchestrator (Fable) assigns and may override; assignment written into
the ticket (new wq `model`,`effort` fields — see BUILD-READY). LC rows: 2-downgrade lines and
haiku's place anywhere above trivial — item 14 settles them.

---
## 16. CONTEXT BUDGET PER TICKET
Mechanics in current CC [WEB code.claude.com/docs/en/model-config.md §context-window, fetched
2026-08-14; env names LOCAL-VERIFIED in binary]:
- Auto-compaction is core-session machinery (autoCompactEnabled/Threshold/Window/autocompactRan
  all in binary [LOCAL strings]); default = compact when the conversation reaches the model's
  context limit; Sonnet 5 proactively at ~967K; 4.6-era models at the 200K boundary. Window
  tunable 100K..1M via /autocompact, `--autocompact` flag, or CLAUDE_CODE_AUTO_COMPACT_WINDOW
  (env wins). DISABLE_COMPACT kills all compaction. Docs frame compaction for sessions
  generally incl. cloud/non-interactive; an explicit "-p compacts: yes/no" sentence is absent
  → [UNRESOLVED-minor: confirm compaction fires in -p via P4's stream capture — expect yes].
- Effective windows for our roster: fable-5 native 1M; sonnet-5 native 1M; opus-5/4.8 1M
  auto-upgrade INCLUDED on Max; haiku-4-5 200K; sonnet-4-6 1M = credits (closed for us).
  1M tokens carry no per-token premium; subscription-covered where included.
- --max-turns interplay: turn cap and compaction are independent; a compact consumes time not
  a user turn. Context exhaustion symptoms when compaction is off/failed: "Prompt is too long"
  error class [WEB model-config.md]; degradation symptoms before that: repeated tool calls,
  re-reading files it already read, shrinking diff quality.
Foreman guidance per archetype: split BEFORE dispatch when the ticket implies touching >~15
files or >2 subsystems or "campaign" language (archetype 6) — decomposition is the
orchestrator's job, not compaction's. Detection of exhaustion-vs-working: token counts from
OTEL per run (input tokens climbing toward window at flat progress), autocompactRan events,
and repeated-identical-tool-call patterns in stream-json; two compactions in one ticket ⇒
foreman flags the ticket oversized and blocks it back for decomposition. Workers on 200K
(haiku) only get archetype-1/2 tickets. Set CLAUDE_CODE_AUTO_COMPACT_WINDOW=500000 for
fable/opus/sonnet workers (compact earlier than 1M keeps behavior sharp and bounds
worst-case token burn per compact).

---
## 10. MODEL×EFFORT CAPABILITY MANIFEST
Full draft at /home/lumen/foreman/research/models.yaml (routing policy + burn estimates +
trust boundaries embedded there). Headline conclusions:
- The 10-model intended roster COLLAPSES to a 4+local routing set on subscription auth:
  fable-5, opus-5, sonnet-5, haiku-4-5, local rung. Reason: opus-4-8/4-7/4-6, sonnet-4-6 offer
  no price edge (subscription burn class ≈ family class; on API opus-5 = opus-4.8 price) and
  strictly-not-better capability than their 5-gen siblings. They stay LISTED (pinning targets,
  Fable's cyber-fallback lands on opus-4-8) but UNROUTED.
- Strictly dominated (model,effort) pairs — never select:
  fable-5@low, fable-5@medium (paying Fable weekly_scoped for sub-Fable reasoning; opus-5@xhigh
  is the honest substitute), every opus-4-x/sonnet-4-6 tuple while their 5-gen sibling is
  selectable, sonnet-5@xhigh-and-max for archetypes 1-2 (overthinking-prone per docs; @high
  ceiling there). Flagged-not-final: opus-5@low vs sonnet-5@high — likely dominated, item 14
  decides. haiku has no effort axis; haiku vs sonnet-5@low is a burn question → item 14.
- Effort-first downgrade CONFIRMED as the right doctrine — the CLI's own runway messaging
  suggests /effort before /model [LOCAL strings], and effort moves burn within a pool without
  changing trust boundaries.

---
## 11. PRIOR ART (patterns to steal, not dependencies)
ON-BOX (highest-value, already fleet-shaped):
- /home/lumen/bin/gh-lane-worker [LOCAL read] — one `claude -p` session per ticket, prompt
  embeds identity + boot files + ONE-ticket loop contract, sentinel lines (LANE_DRAINED /
  CLAIM_ERROR / TICKET_CLOSED) grepped from stdout, `timeout 3600`, launched via lumen-run.
  STEAL: one-session-per-ticket, sentinel contract (superseded by stream-json but keep as
  belt-and-braces), lumen-run launch. FIX: bypassPermissions (§4), sonnet-only, no budget gate.
- wq#175 (done): headless Tier-1 read-only dispatcher, claude -p via LiteLLM→DeepSeek [LOCAL
  episode recall] — proves the §9 rung end-to-end on THIS box.
- wq#502/530/568/582/594/598 machinery: identity registry, provisional leases, retire,
  claim-by-id, dup-driver detection, scratch dirs — the coordination layer most farm projects
  had to invent; ours is already adversarially debugged.
ECOSYSTEM (2026-current, cited earlier in context):
- claude-auto-retry [WEB cheapestinference 2026-07/08] — limit-hit → wait-for-reset → resume
  loop via tmux. STEAL: resume-at-reset policy (§13); we implement in-foreman, no tmux.
- amux "control plane" guides [WEB amux.io 2026-07] — fleet supervision framing: transcripts
  as the audit trail, JSON result parsing, containers for bypass-permissions. STEAL: nothing
  novel for us beyond confirmation.
- Multi-account CLAUDE_CONFIG_DIR tooling (ccam/CCS, AgentsRoom, gists) [WEB 2026-04..08] —
  per-dir credential isolation + per-account usage bars + a "95% usage warning hook so the
  agent saves state before cutoff" [WEB gist KMJ-007 2026-06]. STEAL: the pre-cutoff
  state-save hook idea → our workers get a Stop-adjacent "usage ≥95% → commit WIP + board
  note" behavior driven by the foreman, not by trusting the model.
- claude-code-otel / VM+Grafana recipes [WEB ColeMurray repo; tcude.net 2026-02] — §5 wiring.
- Native `claude agents` view + --bg (2.1.220) [LOCAL --help] — Anthropic's own farm surface:
  dispatch defaults (model/effort/permission-mode), JSON listing. EVALUATE in T2 as the
  supervision substrate before building bespoke process tracking; risk: less control over env
  per worker (needs a spike, 30 min, no quota beyond one dispatched no-op — fold into P7).
- tmux orchestrators / claude-squad / agent-farm lineage (2025) — worktree-per-worker +
  observability panes; our worktree conclusion (§8) matches; no dependency wanted.

---
## PROBES — quota-costing one-liners, ORCH approval required (est. cost each ≈ one tiny
completion unless noted; run serially, off-peak, after 2026-08-19 where burn numbers matter)
P1 roster SERVE-confirmation (RUN AFTER P3 bootstrap; typically 1 completion, not 5):
  Bootstrap model_access[] (P3) already gives entitled+ceiling for opus-5/sonnet-5/opus-4-7/
  opus-4-6 at ZERO quota. Only probe models bootstrap leaves ambiguous — Opus 3 for sure
  (legacy; may be absent from model_access entirely), and 4.7/4.6 ONLY if you want serve
  proof beyond the entitled flag. Guaranteed method if bootstrap is inconclusive:
  for m in claude-3-opus-20240229; do echo "== $m"; claude -p "reply OK" --model "$m" --max-turns 1 --output-format json 2>&1 | head -c 400; echo; done
  Proves: whether a listed-but-retire-dated model actually SERVES (validation server-probe
  path kFt/Dcn) vs tombstone. Expand the loop only for models bootstrap didn't settle.
P2 Fable inclusion + effort accept (1 completion, Fable-pool):
  claude -p "reply OK" --model claude-fable-5 --effort max --max-turns 1 --output-format json 2>&1 | head -c 600
  Proves: Fable included on our Max (no credits demand — else #79337 restart dance), --effort
  max accepted headless, and gives one datapoint of Fable-pool drawdown via P3 before/after.
P3 usage endpoint shape (ZERO quota):
  python3 -c "import json;print(list(json.load(open('/home/lumen/.claude/.credentials.json')).keys()))"   # find token key name first, do NOT print values
  curl -sS https://api.anthropic.com/api/oauth/usage -H "Authorization: Bearer <accessToken>" -H "anthropic-beta: oauth-2025-04-20" | head -c 3000
  # SAME probe also pulls the entitlement + per-model effort-ceiling table (zero quota):
  curl -sS "https://api.anthropic.com/api/claude_cli/bootstrap?entrypoint=cli&model=claude-opus-5" -H "Authorization: Bearer <accessToken>" -H "anthropic-beta: oauth-2025-04-20" -H "anthropic-version: 2023-06-01" -H "User-Agent: claude-code/2.1.220" | python3 -m json.tool | sed -n '1,80p'
  Proves: (a) usage JSON shape (pools/utilization/resets_at) → governor; (b) model_access[]{api_name,entitled,max_effort_level} → which models are entitled + their effort caps, WITHOUT spending completions. Header anthropic-beta oauth-2025-04-20 confirmed from binary [LOCAL wI].
P5 setup-token safety (ZERO quota; QUIET WINDOW — may rotate the live refresh token):
  claude setup-token   # then: CLAUDE_CODE_OAUTH_TOKEN=<new> claude -p "reply OK" --max-turns 1 --output-format json | head -c 300; and re-verify: claude auth status
  Proves: token mints, works headless, existing login survives (issue #48786 class).
P6 local rung end-to-end (ZERO Anthropic quota):
  env -u ANTHROPIC_API_KEY ANTHROPIC_BASE_URL=http://localhost:4000 ANTHROPIC_AUTH_TOKEN=<litellm-foreman-key> claude -p "run: echo rung-ok && cat /etc/hostname" --model deepseek-v4-flash --max-turns 3 --output-format json --allowedTools "Bash(echo *) Bash(cat /etc/hostname)" | head -c 800
  Proves: base-url swap + tool-use translation on our gateway with a real Bash round-trip.
P7 concurrency smoke (3 tiny completions):
  for i in 1 2 3; do (cd /tmp/foreman-r1-363222 && mkdir -p w$i && cd w$i && CLAUDE_CODE_OAUTH_TOKEN=<token> claude -p "reply OK $i" --max-turns 1 --output-format json > out.json) & done; wait; grep -h subtype /tmp/foreman-r1-363222/w*/out.json
  Proves: 3 parallel -p on one static token, distinct cwds, no auth/transcript collision.
  (+30-min no-quota spike: `claude agents --json` after one `claude --bg` no-op — T2 input.)
P4 limit-hit semantics (NO LONGER A BLOCKER — spot-confirm only): the LIMIT-vs-CRASH
  classifier is already derived from decompiled result subtypes + rate_limit_event/api_error
  frames (CLAUDE_CLI_INTERNALS.md §E). Optional confirmation next time §6 shows any pool ≥95%:
  run one sacrificial worker with --output-format stream-json 2>stderr.log, archive the last
  frames + subtype. Not required for T2.

---
## RISKS — top 5 ways this build fails
1. BUDGET VAPORIZATION. Fable/high-effort headless can drain a 5h window in minutes and eat
   the weekly in a day (documented field reports §2; 20-parallel headless = 30% weekly on day
   1 [WEB developersdigest 2026-06-10]). Mitigation IS ticket T1: no worker spawns before the
   governor enforces reserve floors; ≤3 cloud workers; off-peak bias; Fable reserved to
   orchestrator sessions. Extra trap: numbers measured before 2026-08-19 overstate headroom.
2. AUTH CASCADE. Refresh race kills all workers at token expiry (§8, 3 GH issues), and the fix
   (setup-token) itself carries a revoke-the-live-login hazard (#48786) → P5 in a quiet window
   BEFORE T2; single static token; foreman detects auth-class failures and halts cloud lanes.
3. POLICY DRIFT. The line is "ordinary, individual usage" + OAuth-only-in-claude-CLI (§7).
   A 24/7 many-worker farm pattern-matches to what weekly limits exist to stop. Encoded
   mitigations: concurrency cap, waking-hours schedule, limits-are-the-contract (wait, never
   circumvent), OAuth never leaves the claude binary, Agent SDK stays off the OAuth path.
4. PERMISSION BLOWOUT. Inheriting gh-lane-worker's bypassPermissions onto a generalized fleet
   on the prod box = one prompt-injection or one hallucinated command from a house-rule
   violation (reboot/dd/--remove-orphans). T2 ships the §4 pack + guard hook FIRST; a worker
   that can't run under the pack gets its ticket re-scoped, not its guardrails loosened.
5. LIMIT/CRASH CONFUSION. Until P4 pins the -p limit-hit shape, a limit mid-ticket can be
   misread as a crash → foreman retries → burns the recovering window. Interim classifier
   (§13) is deliberately fail-closed: ambiguous ⇒ treat as LIMIT, pause tier, ntfy.
Honorable mentions: entitlement-stale Fable credits bug #79337 (restart worker, don't buy
credits); DC/MCP gateway reaping (foreman runs under lumen-run, never as an MCP child);
Sonnet-5 intro pricing ends 2026-08-31 (API-side only, calibration cost fields shift).

---
## BUILD-READY — the 4-ticket plan, re-cut against findings
T0 (NEW, gates all): PROBES P1,P2,P3,P5,P6,P7 above + Bes VM ingest-URL check. One evening,
   ~10 tiny completions total. Everything below deps on T0.
T1 foreman-usaged (budget governor, an evening): poll §6 primary every 60-90s (CCC fallback,
   fail-closed rule verbatim); pools session/weekly_all/fable_scoped; reserve floors
   (defaults: session ≥20%, weekly ≥15%, Fable-scoped ≥50%-of-its-cap kept for Frank);
   peak-hours calendar (weekday 05-11 PT = dispatch-averse); kill-switch file
   /home/lumen/foreman/STOP; ntfy on floor breach + resets. State: state.json atomic.
   CHANGED vs original plan: primary source is the oauth endpoint, NOT browser/CCC scraping —
   CCC demotes to fallback. Runs under lumen-run.
T2 worker harness (a day): spawn = lumen-run'd `claude -p` with per-ticket worktree cwd,
   (model,effort) from ticket, CLAUDE_CODE_OAUTH_TOKEN env, §4 settings pack (REPLACES
   bypassPermissions — also retrofit gh-lane-worker), stream-json supervisor (init/session_id
   capture, inactivity watchdog, subtype classifier incl. error_max_turns/error_max_budget/
   error_during_execution), per-archetype --max-turns + timeout, OTEL env → Bes VM
   (cumulative temporality + resource attrs), hooks JSONL, wq claim/heartbeat/done/release
   wrapper, resume-after-LIMIT / restart-after-CRASH per §12/§13.
   NEW INPUT: 30-min spike on native `claude --bg`/`claude agents --json` as the supervision
   substrate before building bespoke tracking — adopt only if per-worker env + settings
   injection survive the dispatch path.
T3 orchestrator tier (iterates): Fable-5@high judgment sessions on cadence + burn-down
   triggers (max effort reserved for hard investigations until item-14 numbers justify more);
   reads models.yaml; writes (model,effort) into tickets — wq grows `model`,`effort` columns
   (additive ALTER, wq edit support); agent-prompt emits the tuple header (fable mode already
   exists per wq#580); adversarial verify-pass dispatch per §15 row 8.
   CHANGED: routing set is 4+local (§10), not 10 — mid-Opus ladder unrouted; "Extra"=xhigh
   everywhere in specs; effort-first downgrade codified.
T4 local rung (half day, deps T0/T2): §9 env pack per rung-worker, LiteLLM 'foreman' virtual
   key, discovery flag, archetype restriction (1,2 + verify-drafts), mandatory Sonnet-5
   verify-pass gate before lumen-pr land, lower --max-turns. CHANGED: no router-shim
   dependency; LiteLLM official path.
Plan items adjusted by research: (a) all 10 intended models DO appear in the picker
(screenshot 2026-08-14, incl. Opus 3 — serving = P1) but ROUTING still collapses to 4+local
by domination; (b) effort "Extra" confirmed as the UI label; scriptable string is `xhigh`;
(c) "Fable
credit" as a separate purse ⇒ on Max it's an INCLUDED ≤50%-of-weekly scoped meter (since
07-20), so the orchestrator-on-Fable design is affordable — but floor-guarded; (d) usage
visibility does not need the browser ⇒ CLI-grade endpoint exists (P3); (e) per-worker effort
is trivially scriptable (--effort / CLAUDE_CODE_EFFORT_LEVEL) — no settings.json gymnastics.

---
Closeout: wq#605 done; board note appended; episode committed. Sentinel observed during this
session (NOT this ticket's scope, already alerting on the board): lpp map67 (2,7) wallclock
stall since 01:43Z — pokemon lane's to claim.
— foreman-r1-363222, 2026-08-14 PDT

---
CORRECTIONS 2026-08-14 (post-ship, foreman-r1, source: Frank's Max-session picker screenshot):
(1) effort UI label between High and Max is "Extra" (scriptable string `xhigh` unchanged);
(2) "Opus 3" IS in the model picker → T07 flipped NOT-FOUND→IN-PICKER, P1 elevated to standard
using claude-3-opus-20240229 (dated ID from the binary's embedded docs table, retire-dated
Jan 5 2026 — picker-presence vs retire-date conflict is exactly what P1 resolves).
Lesson: the native binary's strings dump is NOT authoritative for UI label presence/absence.

---
## NOT-APPLICABLE — foreman-tok T0 ledger (wq#629, 2026-08-15, fbuild639-6ad3b8)
Frank forwarded Fable-5 token-reduction research (2 docs, 2026-08-15). Premise filter applied:
we run on Max 20x subscription auth, NO raw Anthropic API key (the one in
/home/lumen/agent/.env is DEFUSED, never used). Every item below is raw Messages-API request/
account surface, gated behind an API key or org-level contract setting — none of it is reachable
through claude CLI's OAuth `-p` surface. Recorded so no future foreman-tok agent re-chases them:

- **`cache_control` breakpoints** — explicit ephemeral-cache markers set in a raw Messages API
  request body, to control which prefix segments get cached. Not exposed via the CLI; the CLI
  manages prompt caching itself. TOKEN_BASELINE.md measured 89-99% cache hit rate across 8
  real sessions with zero manual cache_control anywhere in this stack — nothing to configure.
- **`context-management-2025-06-27` beta** (anthropic-beta header; automatic tool-result
  clearing / context editing) — a raw API request-header opt-in. No equivalent CLI flag; would
  require direct API access we don't have.
- **`task_budget`** — raw Messages API param family for server-side per-request token/turn
  caps. We already have our own budget gate (foreman-usaged, T1/wq#613) sitting in front of
  every dispatch instead; redundant even if it were reachable.
- **server-side model fallback / `fallback_credit_token`** — lets an overloaded primary model's
  request draw from a separate fallback credit pool, billed via API key. Doesn't map onto a
  flat-rate Max subscription (there is no credit pool to draw from). Note this is a DIFFERENT
  mechanism from `--fallback-model` (§1 above), which IS available in -p and IS in use — that
  one is a client-side retry-with-different-model flag, not a billing construct.
- **Batch API** — async bulk-request endpoint (up to 50% API discount for non-interactive
  batches), billed per-token via API key. Every foreman worker call is a live interactive
  `claude -p` dispatch (fworker.py), not an async batchable job, and there's no API key to bill
  against regardless.
- **ZDR (Zero Data Retention)** — enterprise/API contractual data-handling mode, an org-level
  setting tied to API billing agreements. Not applicable to a Max consumer OAuth login.

What we ALREADY have that this research independently recommends (so: verify these keep
working, don't rebuild them):
- context isolation → fworker.py's per-attempt git worktree + wq claim/release lifecycle +
  board line per ticket (BUILD_PLAN.md §T2a).
- requirements ledger → wq ticket `detail` field is the spec; `/home/lumen/agents/work-queue/
  reports/` holds the durable per-ticket writeup (see wq#612's report for the shape).
- effort tiering → models.yaml archetype→(model,effort) routing table + CAL-1 calibration
  (wq#619, scheduled after 2026-08-19) to move rows from estimated to measured.
- resume-not-restart → fworker.py's `handle_limit()`: on a rate-limit hit with a boundable
  reset window, sleeps and calls `--resume <session_id>` rather than re-spawning cold.
- budget governor → foreman-usaged (T1/wq#613): polls `/api/oauth/usage`, reserve floors,
  fail-closed on stale data, `can_dispatch(model)` gate every worker now calls before claiming
  a ticket (wq#639 fixed the gate/claim ordering the same session this ledger was written).

Baseline instrument + numbers for future foreman-tok tickets to diff against:
TOKEN_BASELINE.md / `research/token_baseline.py` (re-run: `python3 token_baseline.py >
token_baseline.json`) — parses the 8 existing `_runs/*.jsonl` real session transcripts
(wq#613/614/625 bootstrap builds), zero new Anthropic spend.
