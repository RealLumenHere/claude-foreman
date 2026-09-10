# wq#1606 — Adversarial verify-pass of wq#1604 (deepseek connect-guard: key windowed counter per model)

**Verdict: CONFIRM** — PR#401 (merged `a496a3c`) does exactly what wq#1604 and the commit
message claim: `events.json` moved from one shared list to a dict keyed by `litellm_model`,
`record_event()`/`should_alert()`/`notify_connect_spike()` all thread the actual model
string through, and the exact wq#1599 repro (20 back-to-back `deepseek-v4-flash` connect
failures interleaved with 30 healthy `deepseek-v4-pro` calls) now alerts instead of being
diluted below threshold. The ntfy message no longer hardcodes "deepseek-v4-flash".

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/401 (merged, `a496a3c` on main).
Diff read cold via `git show a496a3c` against `/home/lumen/foreman` main, not the PR's own
description of itself.

## What the diff does

`ops/wq1590-deepseek-connect-guard/deepseek_connect_alert.py`:
- `record_event(litellm_model, is_failure, now)` (`:148-166`) now loads `events.json` as a
  `dict[model] -> [(ts, is_failure), ...]` (migrating any non-dict legacy shape to `{}`
  rather than crashing on it), appends/prunes only that model's list, and returns
  `windowed_rate()` over that model's events alone.
- `notify_connect_spike(litellm_model, stats)` (`:169-189`) interpolates the actual model
  into both the ntfy title and body instead of the old hardcoded `"deepseek-v4-flash"`
  literal.
- `handle_call_event` (`:192-213`) threads `litellm_model` through `record_event`,
  `should_alert`'s per-key cooldown (unchanged, was already per-model), and
  `notify_connect_spike` consistently.
- `evidence.json` AC1 citation updated to point at the new `record_event` lines.
- 3 new regression tests added (`test_healthy_model_does_not_mask_breaching_sibling`,
  `test_pro_stays_healthy_and_never_alerts_from_flash_failures`,
  `test_message_names_the_actual_breaching_model_not_flash_literal`), 25/25 total.

## Verification performed

1. **Read the diff cold**: full pre/post `deepseek_connect_alert.py`, the 3 new tests, the
   `evidence.json` line change. Confirmed `record_event`/`notify_connect_spike`/
   `handle_call_event` signatures all changed consistently (`grep -n litellm_model`), no
   call site left passing the old 2-arg `record_event(is_failure, now)` shape.
2. **Ran the shipped suite**: `python3 ops/wq1590-deepseek-connect-guard/tests/test_deepseek_connect_alert.py`
   → 25/25 pass, matching the done-notes' claim.
3. **Independently reran the exact wq#1599 repro** against the fixed module (own script, not
   the shipped test) — 20 `deepseek-v4-flash` connect failures interleaved with 30 healthy
   `deepseek-v4-pro` calls, `MIN_SAMPLES=15`/`THRESHOLD=0.5`: alert now fires at the 15th
   flash sample (index 14). Pre-fix (per wq#1599's report) this same shape stayed silent at
   pooled rate 0.40. Confirms the specific dilution defect is closed.
4. **Legacy-state migration probe**: seeded `events.json` with the old flat-list shape
   (`[[ts, bool], ...]`) the pre-fix code would have left on disk, then called
   `handle_call_event` against it. `isinstance(all_events, dict)` guard (`:159-160`) catches
   the non-dict legacy shape and resets to `{}` — no crash, old history silently dropped
   (a one-time, acceptable loss on first deploy of this fix, not a defect).
5. **Adversarial probe — unnormalized dict key**: `is_deepseek_model()` (`:90-93`, unchanged
   by this diff) matches both `"deepseek/"`- and `"deepseek-"`-prefixed strings, and the
   shipped tests exercise both forms (`test_bare_id`, `test_provider_prefixed`) — meaning the
   authors know both spellings occur for what may be the same underlying deployment.
   `research/MODEL_ROUTING_AUDIT.md:191` documents that the same physical rung is referred to
   as both `deepseek-v4-flash` (the registered alias) and `deepseek/deepseek-v4-flash` (the
   resolved provider string at api.deepseek.com). Fed both spellings to `handle_call_event`
   in sequence: they land under two separate dict keys (`['deepseek-v4-flash',
   'deepseek/deepseek-v4-flash']`), not one. **This is a real, reproducible gap**: if
   LiteLLM's callback `kwargs["model"]` ever varies in spelling across calls to the same
   physical deployment (e.g. alias vs. resolved-provider string on a fallback path), the new
   per-model keying would silently split one deployment's failures across two under-threshold
   counters — a milder recurrence of the exact class of bug this ticket closed, just via
   spelling variance instead of cross-deployment pooling.

## Why this does not contradict wq#1604's claim

wq#1604's charter was narrowly "key the windowed counter per `litellm_model` instead of
pooling all deepseek deployments into one" — it does that, faithfully, for whatever string
`kwargs.get("model")` actually contains. Whether that string is stable per deployment is a
LiteLLM-version/runtime question the file's own docstring (`:42-47`) and README (`:46-50`)
already flag as an unresolved "VERIFY on first deploy" item inherited unchanged from wq#1590
— it predates this diff, applies to the whole file, and this sandbox has no live proxy or
`litellm` package to settle it (`_HAVE_LITELLM = False` here). Not fabricated-evidence
territory (Tier-0 instinct 7 checked: both spellings are real, grepped, documented fleet
strings, not values this session invented) — but it's speculative severity pending live
confirmation, not a reproduced production failure the way wq#1599's finding was. Following
the same judgment call as `reports/1601_verify_wq1583_bin_tool_audit.md` (a similarly
out-of-scope, non-blocking adversarial finding logged rather than ticketed): not filing a new
ticket for this — logging it here so whoever verifies the first live deploy (the existing
"VERIFY on first deploy" step already on the README/docstring) checks model-string stability
across the fallback path as part of that pass, rather than opening a second speculative
ticket ahead of any live evidence.

## Disposition

CONFIRM, no escalation to opus-5@high. wq#1604/PR#401's specific, claimed fix (per-model
windowed counter, correct alert attribution) is real, reproduced independently from the
shipped tests, and closes the exact defect wq#1599 found.
