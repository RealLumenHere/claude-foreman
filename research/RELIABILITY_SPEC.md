# RELIABILITY_SPEC — headless worker supervision (claude-foreman)
Frozen 2026-08-15 from deep research (Anthropic docs + claude-code repo issues + harness survey),
cross-validated by wq#645's independent live findings. CLI facts pinned to v2.1.x (v2.1.233 era).
LAW: fworker is not dispatch-law until it passes the full shim matrix below.

## A. CLI contract facts (version-stamped; feature-detect via system/init capabilities, not version strings)
- --max-turns is SUPPORTED (print mode). NOT dropped. (wq#645 confirmed live: accepted.) error on ceiling.
- Exit codes: no exhaustive table published. Known: 0 success; 143 SIGTERM (aborts turn, runs SessionEnd hooks); 137 OOM/SIGKILL; non-zero on error/limit/10MB-stdin-overflow.
- stream-json requires --verbose. NDJSON. Frame types: system, assistant, user, result, stream_event.
- system subtypes: init (session_id, apiKeySource:"none"=OAuth, capabilities[]), api_retry (error in: authentication_failed, oauth_org_not_allowed, billing_error, rate_limit, overloaded, invalid_request, model_not_found, server_error, max_output_tokens, unknown), task_started/task_progress/task_notification (BACKGROUND-TASK lifecycle, NOT usage limits), background_tasks_changed (2.1.203+), task_updated (terminal: completed|failed|stopped|killed; can end a task WITHOUT a following task_notification).
- KNOWN RACE (#39632, unfixed/stale): task_notification/task_updated can be the FINAL frame with the result frame dropped. Last-frame-is-task_notification = INDETERMINATE, never success, never ignorable. (This is our drain-death signature.)
- result subtypes (complete): success, error_max_turns, error_during_execution, error_max_budget_usd, error_max_structured_output_retries. All carry num_turns/usage/session_id/durations/is_error/stop_reason. `result` text only on success; error_* carry errors[]. success CAN have is_error:true (final API call failed) — check api_error_status + terminal_reason.
- terminal_reason (2.1.204+), 19 values. DEAD-TURN set (11): api_error, budget_exhausted, tool_deferred_unavailable, turn_setup_failed, malformed_tool_use_exhausted, structured_output_retry_exhausted, blocking_limit, rapid_refill_breaker, prompt_too_long, image_error, model_error. NOT dead: completed, max_turns, aborted_streaming, aborted_tools, unknown.
  - turn_setup_failed = launch failure BEFORE model work. max_turns = genuine ceiling. NEVER conflate.
- THINKING FRAMES: extended thinking emits its own assistant frame before the content frame — turn counting must count only assistant frames with a non-thinking block (wq#645, live-verified).
- Resume: --resume <session_id> (2.1.223+: searches all projects). Capture session_id from init frame ALWAYS. Transcript at ~/.claude/projects/<sanitized>/<uuid>.jsonl.
- stdout is BLOCK-BUFFERED when piped (#25670): frames can stall in 4-8KB buffer. Not a hang.
- NEVER use --bare on this fleet: bare mode does not read OAuth credentials (subscription auth breaks).
- Grace: bg bash killed ~5s after result+stdin close (2.1.163+); bg subagents waited up to 10min (CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS, 2.1.182+); slow consumer drain capped 30s (2.1.214+).

## B. Max-20x limit behavior (POLICY-VOLATILE, re-verify before hardcoding)
- Headless shares the SAME 5h + weekly pool as interactive/claude.ai. Top-model weekly bucket is separately much smaller (our fable_weekly_scoped). June-15-2026 programmatic billing split was announced then PAUSED — still shared pool today; may return with notice.
- Limits manifest as: system/api_retry error:rate_limit/overloaded and/or result with api_error_status 429 + dead-turn terminal_reason (api_error/blocking_limit/rapid_refill_breaker). rate_limit_event utilization omitted when status=allowed (#50518) — no live per-bucket % from inside a session.
- /api/oauth/usage: UNDOCUMENTED, 429s aggressively under polling (#31021/#31637), may not recover for hours. Governor MUST: poll >= minutes apart, cache last-good, treat 429 as reuse-cache, never fail-closed on one stale read (fworker bug 1 class).

## C. Failure taxonomy -> verdict -> response (canonical)
1 SUCCESS: result:success + is_error:false + terminal_reason:completed + exit 0 -> commit, release claim.
2 TURNS: result:error_max_turns OR terminal_reason:max_turns, AND >=1 real assistant turn -> resume w/ higher ceiling or split. Never legal without a result frame.
3 LAUNCH_FAILURE: no init, or init->instant death, or terminal_reason:turn_setup_failed; tiny CPU corroborates -> fix invocation/env/auth; capped backoff retry; NEVER TURNS.
4 LIMIT: api_retry rate_limit/overloaded, 429, blocking_limit/rapid_refill_breaker -> pause dispatch till window reset (cached usage), resume after. No crash-loop.
5 KILLED: exit 143, no result -> identify SIGTERM sender (systemd timeout/OOM path/wrapper); resume if idempotent.
6 OOM: exit 137 or journal oom-kill -> raise MemoryMax or shrink; resume.
7 INDETERMINATE: last frame task_notification/task_updated/assistant, NO result -> never success; resume by session_id, verify via artifacts, then verdict.
8 STALL: no frames > T_gap, process alive -> SIGTERM (becomes 5) -> resume.
9 SUCCESS-BUT-STUCK: result:success, process never exits (#25629) -> accept result, kill process, release.
10 FALSE-COMPLETE: result:success but acceptance criteria unmet / "check back shortly" -> re-drive (--continue/resume); verify against criteria, never the model's self-report. Stop-hook (exit 2, guard stop_hook_active) helps but does NOT fire on silent tool stops (#29881) — harness verification is the real gate.
11 AUTH: api_retry authentication_failed/401 -> refresh OAuth, resume.
12 DEGRADED-SUCCESS: result:success with empty result text (#7124) -> read artifacts/transcript.
13 NOT-A-FAILURE: buffered-pipe burst (#25670) -> line-buffered reads + liveness check before declaring STALL.

## D. Verdict engine invariants (illegal states unrepresentable)
- No verdict without evidence: TURNS requires result+real-turns; SUCCESS requires result:success; absence of result can only map to KILLED/OOM/LAUNCH_FAILURE/STALL/INDETERMINATE.
- Exit code cross-check mandatory (0/143/137/other vs frame verdict; disagreement -> INDETERMINATE + flag).
- Wall-clock/CPU are corroboration only, never primary classification.
- Every terminal verdict releases-or-requeues the claim. No path ends holding a lease.

## E. Watchdogs + systemd
- Frame-gap watchdog primary (T_gap 120-300s, above slowest legit tool). Wall-clock ceiling secondary (SIGTERM->SIGKILL). CPU never a ceiling.
- systemd-run units: MemoryMax + MemoryHigh + TasksMax + CPUQuota; OOMPolicy=stop; TimeoutStopSec>=60 (let SessionEnd hooks run to a clean 143); persistent journal for OOM forensics.
- External verdict-aware supervision (foreman) over Restart=always; if Restart used: on-failure + StartLimitIntervalSec/Burst (router #1341 crash-loop pathology).
- Lease TTL + independent reaper decoupled from graceful release (fq#614 fix).

## F. fake-claude shim test matrix (all 16 must pass before fworker is dispatch-law)
1 clean success; 2 real turn ceiling->TURNS; 3 no-init instant exit->LAUNCH_FAILURE never TURNS; 4 turn_setup_failed->LAUNCH_FAILURE; 5 429/rate_limit->LIMIT+pause; 6 exit143 no result->KILLED; 7 exit137->OOM; 8 task_notification-last no-result exit0->INDETERMINATE; 9 mid-task silence>T_gap->watchdog kill; 10 result-then-never-exit->SUCCESS+forced kill+release; 11 success w/ unmet criteria->FALSE-COMPLETE; 12 401->AUTH; 13 empty result text->DEGRADED-SUCCESS; 14 buffered burst->no false STALL; 15 partial-line NDJSON reassembly; 16 unknown frame type->raw fallthrough, no crash.
Plus wq#645's regression pair: thinking-frame turn counting.

## G. Rollout stages
S1 (wq#646): verdict engine per C+D, shim 1-8. S2 (wq#647): watchdogs+TimeoutStopSec+lease reaper+false-complete verification, shim 9-13. S3 (wq#648): governor cache-on-429+bucket separation audit, NDJSON parser hardening, OOM forensics, shim 14-16.
