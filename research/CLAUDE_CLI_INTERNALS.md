# CLAUDE CODE 2.1.220 — MODEL + EFFORT INTERNALS (decompiled)
foreman-r1, 2026-08-14. Source: the minified JS bundle is embedded verbatim in the native
binary (`bin/claude.exe`), recoverable via `strings`. Every claim below tagged [SRC fn] cites
the actual decompiled function name in that bundle — this is the real control flow, not doc
inference. NO code copied out; this is a behavioral map for building claude-foreman's
deterministic (model, effort) selection. Cross-ref: FOREMAN_RESEARCH.md §2/§3/§6.

Interop note: read-only inspection of a locally-installed CLI to learn its own public flags,
env vars, and API-response shapes. Nothing extracted, redistributed, or reimplemented; the goal
is to drive the official CLI correctly and unattended.

================================================================================
## A. EFFORT RESOLUTION — the exact algorithm (this is the "select correctly" answer)
================================================================================
Canonical ladder [SRC EL]: `["low","medium","high","xhigh","max"]` (index 0..4). Frank 1..5 =
these in order; slot 4 UI label "Extra", wire string `xhigh`.

Core resolver [SRC Uoe(model, fallbackEffort)] — returns the effort actually sent, or undefined
if the model has no effort axis:
  1. If !OI(model) → undefined (Haiku etc.; launch with no effort).
  2. r = XCe(model)  // is a launch-effort PIN active for this model?
     n = ait(model)  // model's own default_effort (catalog ?? "high")
     o = iit()       // CLAUDE_CODE_EFFORT_LEVEL env, parsed (null if "unset"/"auto"/absent)
  3. If o===null AND !r → undefined (no env, no pin → caller supplies its own default).
  4. effort = o ?? (r ? n : undefined) ?? fallbackEffort ?? n
     → ENV WINS OUTRIGHT. Then pinned-default, then the caller's value, then model default.
  5. Clamp to org ceiling: YCe(effort, model)  [SRC kQt reads model_access.maxEffortLevel].
  6. If "max" but model lacks max → "high"  [SRC eqe]. If "xhigh" but model lacks xhigh →
     "high"  [SRC I_e]. (Never silently downgrades to an ADJACENT tier — jumps to high.)

PRECEDENCE (highest→lowest), verified from Uoe + IQt + iit:
  CLAUDE_CODE_EFFORT_LEVEL env  >  launch --effort / persisted effortLevel  >  model default
The env var is the master lever: if set, it beats a differing `--effort` on the same command.
For the foreman this is the deterministic pin — set it in the worker's child env and the turn's
effort is fixed regardless of any in-session `/effort` (which reports "Not applied" against it).

Parsers, so the foreman knows what each channel accepts:
- `--effort <v>` flag [SRC uJn→uHt]: accepts low/medium/high/xhigh/max, the alias `med`, and
  `ultracode`(→xhigh). Anything else → ignored + warning "Valid values: low, medium, high,
  xhigh, max". NO integers on the flag.
- `CLAUDE_CODE_EFFORT_LEVEL` env [SRC iit→gW]: accepts the names above, `med`, the sentinels
  `unset`/`auto` (→ fall through to model default), AND bare integers (parsed to a number). The
  integer→name path is not cleanly mapped downstream (z1e returns "high" for a number), so
  **use NAMES in the env var, never integers.** Frank's "4" means the string `xhigh`.
- settings `effortLevel` [SRC eWR]: persisted; enum low/medium/high/xhigh only (no max — max is
  session/env-only). Community-reported that settings upgrades can strip the key; don't rely.
- agent/skill frontmatter `effort:` [SRC _0m]: enum names OR integer; overrides session effort
  for that subagent, but NOT the env var.

Per-model effort CEILING [SRC OI / eqe / I_e] — decompiled exclusion lists, authoritative:
  OI  (has effort at all) excludes: claude-3-*, opus-4-0, opus-4-1, sonnet-4-0, sonnet-4-5,
      haiku-4-5.  → Haiku 4.5 has NO effort axis.
  eqe (supports max)      excludes: claude-3-*, opus-4-0/4-1/4-5, sonnet-4-0/4-5, haiku-4-5.
  I_e (supports xhigh)    excludes: claude-3-*, opus-4-0/4-1/4-5/**4-6**, sonnet-4-0/4-5/**4-6**,
      haiku-4-5.
  Net for our roster:
    fable-5, opus-5, opus-4-8, opus-4-7, sonnet-5  → [low,medium,high,xhigh,max]
    opus-4-6, sonnet-4-6                            → [low,medium,high,max]  (xhigh→high)
    haiku-4-5                                       → none
  Override knob: `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1` forces OI true even for excluded models
  [SRC OI] — an escape hatch; do NOT use on the fleet (unsupported combos are undefined).

Launch-effort PIN [SRC XCe / yW] — the "first-run holds the default" trap, exact scope:
  Pins apply ONLY to opus-4-7, opus-4-8, and fable-5 (guarded by unpinOpus47/48/Fable5
  LaunchEffort flags). Opus-5 and Sonnet-5 are NOT pinned. Passing an explicit `--effort` runs
  yW() which clears ALL three pins globally [SRC IQt→gW→yW]. → Foreman rule: every fable-5 /
  opus-4-8 / opus-4-7 dispatch passes `--effort` explicitly; the pin then never bites.

Effort semantics (for models.yaml annotations) [SRC uug]: low="minimal overhead"; medium=
"balanced, standard testing"; high="comprehensive, extensive testing/docs"; xhigh="deeper than
high, just below max"; max="deepest reasoning; may overthink / burn excessive tokens — use
sparingly." A/B nudge string [SRC _Wi + tengu_slate_finch]: high description can gain "· burns
fastest — medium handles most tasks" → Anthropic's own copy couples effort to burn.

VERIFICATION HOOK (ground-truth, free): the resolved per-turn effort is exposed to hooks and to
Bash as the `CLAUDE_EFFORT` env var, and appears in the hook payload `effort.level` [SRC h0].
Also `get_settings` control response `applied.effort` [SRC eWR] is the runtime-resolved value.
→ A worker's guard/Stop hook can echo `$CLAUDE_EFFORT` into the foreman's events JSONL so the
foreman confirms every worker ran at the intended effort — no guessing.

================================================================================
## B. MODEL RESOLUTION — aliases, entitlement source, what "picker" actually does
================================================================================
Alias → wire ID [SRC picker builders HWi/XBc/AJn/JBc + lgi.resolvedModel]:
  fable → claude-fable-5   opus → claude-opus-5   sonnet → claude-sonnet-5
  haiku → claude-haiku-4-5 (alias carries no version guarantee; pin the dated ID to be safe)
  default → account default (= claude-opus-5 on Max)   best → Fable where available
  opusplan → Opus in plan mode, Sonnet to execute   [1m] suffix = 1M-context variant
  Env pins [SRC all present]: ANTHROPIC_DEFAULT_{FABLE,OPUS,SONNET,HAIKU}_MODEL(_NAME/_DESC),
  ANTHROPIC_MODEL, ANTHROPIC_CUSTOM_MODEL_OPTION, CLAUDE_CODE_SUBAGENT_MODEL.
  ANTHROPIC_SMALL_FAST_MODEL is deprecated → DEFAULT_HAIKU.

ENTITLEMENT — the authoritative source is the bootstrap fetch, NOT the picker [SRC xmt / Nl_ /
$l_ schema]: `GET {BASE}/api/claude_cli/bootstrap` (OAuth bearer + `anthropic-beta` header)
returns `model_access: [{api_name, entitled: bool, max_effort_level?}]`, plus
`additional_model_options`, `additional_model_costs`, `org_model_default`, `auto_compact_windows`,
`oauth_account`. This is cached to ~/.claude.json (modelAccessCache / additionalModelOptionsCache
/ orgModelDefaultCache). → The cleanest "which models can I actually run, and their effort
ceilings" probe reads THIS, no completion spend. (P3-adjacent; add to T0.)

Picker filtering [SRC pit / Fug / jug / MWi]:
  - Builds the tier-appropriate base list, then appends: ANTHROPIC_CUSTOM_MODEL_OPTION, any
    gateway-discovered options, server `additional_model_options`, and enterprise
    `availableModels` allowlist entries.
  - `availableModels` (enterprise managed setting) [SRC enforceAvailableModels]: when set,
    constrains selectable models AND the Default; empty/invalid allowlist → only the default
    model is available. N/A for Frank (no managed settings) but explains any future clamp.
  - `org_model_default.override_user_selection` [SRC X2s]: org can force a default and override
    the user's saved pick. N/A for Frank.
  - Rows can be present-but-disabled (visible, not selectable) with the reason folded into the
    description [SRC nqe.disabled] — e.g. ZDR exclusions, or Fable "requires usage credits" when
    entitlement says credits-only [SRC Gug + kWi=" · Requires usage credits"].

Model VALIDATION at selection [SRC kFt / Dcn]: `/model X` (and `-p --model X`) server-probes
with a 1-token request when X isn't a known-good alias. Failure modes returned:
  retired/unknown ID → "not found" (notFound=true); org-restricted → "restricted by your
  organization"; opus[1m]/sonnet[1m] not entitled → specific 1M-unavailable message. A retired
  model is a NOT-FOUND, which does NOT trigger --fallback-model (that's for server/overload
  errors only). → For T07 (Opus 3): the picker shows it, but selection will server-probe
  claude-3-opus-20240229; expect notFound if truly retired. P1 confirms serve-vs-tombstone.

================================================================================
## C. WHAT MODELS/EFFORTS "POINT TO" — refusal-fallback routing (decompiled table)
================================================================================
When a model ends a turn with stop_reason "refusal", the CLI can silently retry the SAME turn
on a different model, per category [SRC FQt route tables + Yug selector]:
  DEFAULT routes (Vug), used when the refusing model is Fable 5 / most models:
      bio   → claude-opus-5
      cyber → claude-opus-4-8
  If the refusing model IS opus-5 / opus-5[1m] (zug): cyber → claude-opus-4-8; bio → (absent →
      no fallback → hard refusal).
  kWr special-model case (Kug): bio → claude-opus-4-8; cyber → claude-opus-4-8.
Consequences for the fleet:
  • claude-opus-4-8 MUST stay entitled — it is the cyber-refusal landing zone for every tier.
    (Confirms FOREMAN_RESEARCH.md §2/§10 "keep 4.8 as Fable's cyber fallback target," now with
    the exact table + the opus-5-origin asymmetry.)
  • A Fable-5 worker that trips a bio classifier silently continues on opus-5; a cyber trip
    continues on opus-4-8. The foreman sees this as a `model_refusal_fallback` system message
    [SRC tdE] carrying original_model, fallback_model, api_refusal_category,
    retracted_message_uuids. A `model_refusal_no_fallback` [SRC rdE] means the retry was
    declined (no mapped target, or catch-all disabled) → the turn ends refused.
Gates / knobs [SRC nV / i2c / Jug]:
  - `CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1` disables the whole mechanism.
  - `switchModelsOnFlag` setting (default true) toggles the persistent-swap behavior.
  - `--fallback-model M` arms a catch-all (fires on overload/server errors AND as the
    unmapped-refusal catch-all unless `CLAUDE_CODE_REFUSAL_FALLBACK_CATCH_ALL` is disabled).
  - In `-p` there is NO consent dialog: supportedDialogKinds fails closed → the refusal path is
    taken automatically (retry on the mapped model) or errors; Fable's usage-credit consent
    prompt likewise never appears in -p [SRC K1e / EWi / u5R model_consent_fallback].
Related model-swap signals the foreman should parse in stream-json:
  `model_fallback` [SRC c5R] triggers: model_not_found | permission_denied | overloaded |
  server_error | last_resort | model_blocked (turn-scoped; primary retried next turn).
  `api_retry` [SRC ZuE]: {attempt, max_retries, retry_delay_ms, error_status, error-category}.

================================================================================
## D. USAGE / LIMIT SIGNALS — in-band, no browser, no extra auth
================================================================================
BIG REFINEMENT vs FOREMAN_RESEARCH.md §6/§13: the workers ALREADY emit usage + limit state in
their own stream-json — the foreman does not have to poll a separate endpoint to know the wall.

(1) `rate_limit_event` frame on the wire [SRC BuE / juE], emitted whenever limit info changes:
    { status: "allowed"|"allowed_warning"|"rejected",
      rateLimitType: "five_hour"|"seven_day"|"seven_day_opus"|"seven_day_sonnet"|
                     "seven_day_overage_included"|"overage",   // the binding pool
      resetsAt: <epoch>, utilization: <0..1>,
      overageStatus, overageResetsAt,
      overageDisabledReason: [... "out_of_credits" ...],   // matches Frank's extra-usage OFF
      errorCode: "credits_required"?, canUserPurchaseCredits?, rateLimitGraceActive? }
    → The governor watches these live: `allowed_warning` = approaching wall on `rateLimitType`;
      `rejected` = wall hit on that pool with `resetsAt`. Per-request, free, no polling.
(2) `get_usage` control response [SRC U5R] (full snapshot; SDK/remote control path):
    rate_limits: { five_hour, seven_day, seven_day_oauth_apps, seven_day_opus, seven_day_sonnet,
      model_scoped: [{ display_name, utilization, resets_at }],   // ← the Fable bucket lives here
      extra_usage: { is_enabled, monthly_limit, used_credits, utilization, currency } }
    plus subscription_type, rate_limits_available. Same claude.ai data the /usage screen shows.

================================================================================
## E. LIMIT-vs-CRASH — P4 is now largely ANSWERED from source (deterministic classifier)
================================================================================
The `-p` result frame subtypes are decompiled [SRC WuE success + quE error]:
  success:  subtype "success", is_error:false
  error:    subtype ∈ { "error_during_execution", "error_max_turns", "error_max_budget_usd",
                         "error_max_structured_output_retries" }, is_error:true, errors[]
There is NO dedicated "limit" subtype. A usage-limit hit in -p therefore surfaces as
`error_during_execution`, but it is ALWAYS preceded in the same stream by either:
  • a `rate_limit_event` with status "rejected" (+ resetsAt), or
  • an `api_error` system message [SRC m5R] whose `error.rate_limits` is non-null
    (`{resets_at, rate_limit_type}`), or an `api_retry`/error carrying error-category
    `"rate_limit"` [SRC f_l enum includes "rate_limit"].
⇒ FOREMAN CLASSIFIER (no need to force a real limit to learn the shape):
    LIMIT  ⇐ result is error AND (saw rate_limit_event.status=="rejected"  OR
             api_error.rate_limits!=null  OR  error category=="rate_limit").  Use resetsAt.
    BUDGET ⇐ subtype=="error_max_budget_usd"   (from --max-budget-usd).
    TURNS  ⇐ subtype=="error_max_turns"        (from --max-turns).
    CRASH  ⇐ any other error with no rate-limit signal in the stream.
  Still fail-closed: ambiguous → LIMIT (pause tier). P4 downgraded from "must force" to "spot-
  confirm opportunistically" — the branches are known.
Exit code: the classifier keys on the JSON `subtype`/frames, not the shell exit code (which the
strings do not pin unambiguously). Always parse `--output-format stream-json` (or json) — do not
depend on exit code alone.

================================================================================
## F. CONTEXT / AUTOCOMPACT + a few more active knobs
================================================================================
Autocompact is core-session [SRC autoCompact* + Zhr /autocompact handler]:
  window set by `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (env, highest precedence — the handler says so
  verbatim: "CLAUDE_CODE_AUTO_COMPACT_WINDOW is set and takes precedence") OR `/autocompact
  [auto|<tokens>]` OR settings `autoCompactWindow`; accepts auto or 100k–1M (200 = 200k
  shorthand); capped to the model's limit. `autoCompactEnabled` toggles it. Compaction fires in
  -p too (it is session machinery, not TUI-only); emits `compact_boundary` [SRC YuE] with
  pre/post token counts. → Set CLAUDE_CODE_AUTO_COMPACT_WINDOW=500000 for fable/opus/sonnet
  workers (bounds worst-case burn per compact, keeps behavior sharp).
Thinking [SRC RQt/DQt/R_e]: adaptive thinking always-on for fable-5/sonnet-5/opus-4.7+; cannot
be disabled on those. MAX_THINKING_TOKENS + adaptive-disable only affect the 4.6-era pair.
Gateway discovery [SRC Bl_]: with provider=gateway AND
`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`, the picker is populated from the gateway's
`GET /v1/models` (used by the LiteLLM local rung — §9 of FOREMAN_RESEARCH). Model-name
validation is skipped on the gateway path, so any alias string passes through.
Permission modes [SRC Het]: default | acceptEdits | bypassPermissions | plan | dontAsk | auto
(auto = Sonnet-classifier gate). bypassPermissions requires the accepted-disclaimer flag
[SRC eBc / sTm] — another reason the foreman uses the acceptEdits + deny-rules + guard-hook pack
(FOREMAN_RESEARCH §4) rather than bypass.

================================================================================
## G. NET DELTAS this dissection adds to the build plan
================================================================================
1. Effort selection is deterministic and env-pinnable: set CLAUDE_CODE_EFFORT_LEVEL=<name> in
   the worker child env for a hard pin (beats --effort and any /effort); still pass --effort on
   fable/opus-4.x to clear the launch pin. Names only, never integers.
2. Entitlement + per-model effort ceilings come from /api/claude_cli/bootstrap (model_access[]);
   read it once at T0 instead of picker-scraping. No completion spend.
3. Refusal routing is a fixed table (bio→opus-5, cyber→opus-4-8; opus-5-origin bio has none) —
   keep opus-4-8 entitled; parse model_refusal_fallback / _no_fallback in the supervisor.
4. Usage/limit is IN-BAND: rate_limit_event + api_error frames give status/pool/resetsAt live;
   the separate poll (oauth endpoint / CCC) becomes a backstop for idle periods, not the primary.
5. LIMIT-vs-CRASH classifier is knowable from stream frames now (§E) — P4 no longer gates T2.
6. Verify actual effort per worker via the CLAUDE_EFFORT env / hook effort.level — free
   ground-truth, wire it into the events JSONL.
— foreman-r1-363222
