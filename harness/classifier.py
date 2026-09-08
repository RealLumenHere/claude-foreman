"""Stream-json event classifier — RELIABILITY_SPEC.md §C/§D verdict engine.
S1 (wq#646): SUCCESS, TURNS, LAUNCH_FAILURE, LIMIT, KILLED, OOM, INDETERMINATE, shim 1-8.
S2 (wq#647): AUTH, DEGRADED_SUCCESS, HANG now carries §C8's STALL semantics (resumable,
not a fresh restart — see fworker.handle_resumable_failure), shim 9-13. FALSE_COMPLETE
is exported here as the shared outcome vocabulary, but classify() never returns it —
"acceptance criteria unmet" isn't a stream-json shape, it's a fact about the ticket/repo
after the fact, so fworker.reconcile()'s check_artifacts_done() decides it (§C10: verify
against artifacts, never the model's self-report).

RELIABILITY_SPEC.md is frozen law for this harness (cross-validated by wq#645's live
findings). §D's governing invariant: no verdict without evidence. A verdict that would
otherwise rest on absent evidence must fall to the most conservative reading — never
guessed as SUCCESS, never silently folded into a generic CRASH bucket once the spec
gives us a sharper answer.

REFUSED/BUDGET/CRASH remain coarse catch-alls for shapes neither S1, S2 nor S3 (wq#648:
NDJSON parser hardening — reader-level, not a new verdict; shim 14-16 all resolve to
SUCCESS/HANG on the existing verdict set) has a sharper spec-mandated answer for yet.
"""

import re

SUCCESS = "SUCCESS"
REFUSED = "REFUSED"
TURNS = "TURNS"
LAUNCH_FAILURE = "LAUNCH_FAILURE"
BUDGET = "BUDGET"
LIMIT = "LIMIT"
KILLED = "KILLED"
OOM = "OOM"
INDETERMINATE = "INDETERMINATE"
CRASH = "CRASH"
HANG = "HANG"
AUTH = "AUTH"
DEGRADED_SUCCESS = "DEGRADED_SUCCESS"
FALSE_COMPLETE = "FALSE_COMPLETE"
# wq#1107: the loop breaker stopped the session, but the repeated failure was this
# harness's own permission configuration refusing a tool call — the run says nothing
# about the model or the ticket. Never produced by classify() (which only sees the
# child's frames and exit code); fworker.py overrides the raw KILLED verdict with this
# when action_gate.LoopTracker reports trip_kind == "PERMISSION", the same way it
# overrides to SUCCESS for killed_reason == "ticket_done".
PERMISSION_LOOP = "PERMISSION_LOOP"

# wq#1442 (evidence: wq#1436/wq#1434, both --model opus-5, died in 1.9s/1 turn/$0
# cost): the CLI's own --model preflight rejects an id/alias it doesn't recognize
# as a "success"-shaped result frame (subtype "success", but `is_error` is
# omitted rather than explicitly false) with a friendly result_text — before any
# real model turn runs. That fails classify()'s SUCCESS branch (§C1 needs
# `is_error is False`, not merely absent) and used to fall into the generic CRASH
# catch-all, charging an attempt and blocking UNKNOWN:CRASH against a bad model
# string the harness itself dispatched. Bucketed HARNESS_DEFECT like
# PERMISSION_LOOP: retrying hits the identical 404, so the run is no evidence
# about the ticket or the model.
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"

# wq#749: harness/prompts/worker.md has the model print exactly one of these as the
# LAST line of its final message. Nothing parsed it before wq#749 — the sentinel
# existed only as prose in the prompt (design/T2A_WORKER_HARNESS_DESIGN.md §3: "belt-
# and-braces... the result frame is the real signal"), so a worker that correctly
# called `wq block` and exited 0 produced an ordinary result:success frame, which
# fworker.do_attempt() then relabeled FALSE_COMPLETE (ticket status != "done") and
# retried — burning an attempt on a ticket that had already been legitimately blocked.
TICKET_CLOSED_SENTINEL = "TICKET_CLOSED"
BLOCK_SENTINEL = "BLOCKED"
CLAIM_ERROR_SENTINEL = "CLAIM_ERROR"
_WORKER_SENTINELS = frozenset({TICKET_CLOSED_SENTINEL, BLOCK_SENTINEL, CLAIM_ERROR_SENTINEL})


def parse_worker_sentinel(result_text):
    """Last non-empty line of the model's final message, if it's one of the three
    sentinels worker.md mandates; else None. A recognized sentinel is NOT sufficient
    on its own to trust the outcome it claims (§C10: never trust a self-report) —
    callers must corroborate it against actual ticket/repo state before acting on it."""
    if not isinstance(result_text, str):
        return None
    for line in reversed(result_text.strip().splitlines()):
        line = line.strip()
        if line:
            return line if line in _WORKER_SENTINELS else None
    return None

# §C12 DEGRADED-SUCCESS (#7124) fires only when a result frame explicitly carries an
# EMPTY result text — most result frames in these fixtures/real traffic omit the key
# entirely (it is absent on non-success subtypes, and plenty of legitimate successes
# never populate it either), which must stay plain SUCCESS. A sentinel distinguishes
# "key never seen" from "key seen, empty string" so observe() doesn't have to guess.
_UNSET = object()

# wq#1442: the CLI's exact friendly wording for an unrecognized --model id/alias
# (live evidence: wq#1436/wq#1434 result_text "There's an issue with the selected
# model (opus-5). It may not exist or you may not have access to it. Run --model
# to pick a different model."), plus the raw API error shape RESEARCH docs
# (CLAUDE_CLI_INTERNALS.md §C model_fallback triggers) name for the same failure,
# in case a resumed/mid-session 404 ever surfaces it as a structured error instead
# of this preflight text.
_MODEL_NOT_FOUND_RE = re.compile(
    r"issue with the selected model|model_not_found|not_found_error", re.I)


class RunState:
    """Accumulates the signals classify() needs while a child's stdout is parsed
    line by line. One instance per attempt."""

    def __init__(self):
        self.session_id = None
        self.model = None
        self.permission_mode = None
        self.saw_init = False
        self.saw_limit = False
        self.latest_resets_at = None
        self.saw_refusal_no_fallback = False
        # wq#617 refusal awareness (CLAUDE_CLI_INTERNALS §C): every model-swap frame the
        # CLI emits, so the orchestrator can see WHERE a refused turn landed (bio ->
        # opus-5, cyber -> opus-4-8) even when the run still classifies SUCCESS.
        self.refusal_fallbacks = []      # [{original_model, fallback_model, category}]
        self.model_fallbacks = []        # [{reason, fallback_model}]
        self.api_retries = 0
        self.compact_boundaries = 0
        self.result_subtype = None
        self.result_is_error = None
        self.result_text = _UNSET
        self.terminal_reason = None
        self.last_frame_type = None
        self.last_event_ts = None
        self.hung = False
        self.turns = 0
        self.turns_exceeded = False
        self.saw_auth_failure = False
        self.saw_model_not_found = False
        # observability only (wq#754/wq#736) — never fed into classify(), just carried
        # through to the attempt record / usage sidecar for cost, duration and token
        # visibility.
        self.usage = None
        self.total_cost_usd = None
        self.duration_ms = None
        self.num_turns = None

    def observe(self, frame):
        """Feed one parsed stream-json frame (dict). Returns nothing; mutates state."""
        ftype = frame.get("type")
        if ftype is not None:
            self.last_frame_type = ftype

        if ftype == "system" and frame.get("subtype") == "init":
            self.saw_init = True
            self.session_id = frame.get("session_id")
            self.model = frame.get("model")
            self.permission_mode = frame.get("permissionMode") or frame.get("permission_mode")

        # RELIABILITY_SPEC.md §A: --max-turns IS supported in print mode (NOT dropped
        # in 2.1.233 — that belief was wrong; wq#645 confirmed live it is accepted and
        # errors on ceiling, PROBE_RESULTS.md). fworker passes --max-turns so the CLI
        # itself emits the terminal result:error_max_turns frame §C2 requires as TURNS
        # evidence. The supervisor also counts real turns itself (below) purely as a
        # backstop watchdog (fworker.py's ARCHETYPE_MAX_TURNS + a small buffer) in case
        # the CLI doesn't self-enforce for some model/version combo — if that backstop
        # has to SIGTERM the child, no result frame arrives and classify() correctly
        # falls through to the no-result branch (KILLED, not TURNS; §D: TURNS is never
        # legal without a result frame).
        #
        # wq#645: one agentic-loop turn is NOT one "assistant" frame. With extended
        # thinking on, the CLI emits a *separate* assistant frame whose content is
        # solely a "thinking" block, then a second assistant frame with the real
        # tool_use/text block — both frames share one stop_reason. Counting every
        # assistant frame double-counted real turns 2x, so fworker's self-enforced
        # backstop fired at ~half the intended max_turns and misclassified genuinely
        # in-progress workers as TURNS (live evidence: wq#626/wq#603 attempts hit
        # turns==max_turns after ~40 frames that were only ~20-22 real turns). Only
        # count a frame that carries a non-thinking content block.
        if ftype == "assistant":
            content = (frame.get("message") or {}).get("content") or []
            block_types = {c.get("type") for c in content if isinstance(c, dict)}
            if block_types - {"thinking"}:
                self.turns += 1

        # rate_limit_event can arrive as its own frame type or nested in a system message
        rle = frame if ftype == "rate_limit_event" else frame.get("rate_limit_event")
        if rle:
            if rle.get("status") == "rejected":
                self.saw_limit = True
                self.latest_resets_at = rle.get("resetsAt") or rle.get("resets_at")

        if ftype == "api_error" or frame.get("api_error"):
            err = frame.get("error") or frame.get("api_error") or {}
            if err.get("rate_limits") is not None:
                self.saw_limit = True
                rl = err.get("rate_limits") or {}
                self.latest_resets_at = self.latest_resets_at or rl.get("resets_at")
            if err.get("category") == "rate_limit":
                self.saw_limit = True
            # wq#1442: a genuine API-level 404/model_not_found (as opposed to the CLI's
            # own preflight text caught in the result-frame branch below) — same shape
            # the rate_limit/401 checks above already read err.get("category")/status
            # from.
            if err.get("category") == "model_not_found" or err.get("type") == "not_found_error":
                self.saw_model_not_found = True

        # api_retry (and any other frame) can carry error-category "rate_limit" at the
        # top level, or a bare 429, per RELIABILITY_SPEC.md §B.
        if frame.get("category") == "rate_limit" or frame.get("error_category") == "rate_limit":
            self.saw_limit = True
        if frame.get("error_status") == 429 or frame.get("api_error_status") == 429:
            self.saw_limit = True
        if frame.get("error_status") == 404 or frame.get("api_error_status") == 404:
            self.saw_model_not_found = True

        if ftype in ("model_refusal_no_fallback",):
            self.saw_refusal_no_fallback = True
        if ftype == "model_refusal_fallback":
            self.refusal_fallbacks.append({
                "original_model": frame.get("original_model") or frame.get("originalModel"),
                "fallback_model": frame.get("fallback_model") or frame.get("fallbackModel"),
                "category": frame.get("api_refusal_category") or frame.get("category"),
            })
        if ftype == "model_fallback":
            self.model_fallbacks.append({
                "reason": frame.get("reason") or frame.get("trigger"),
                "fallback_model": frame.get("fallback_model") or frame.get("fallbackModel"),
            })
        if ftype == "api_retry":
            self.api_retries += 1
            # §C11 AUTH: system/api_retry error in {authentication_failed} or a bare 401,
            # same frame shape §B/§C4's rate_limit/429 detection already reads from.
            if (frame.get("error") == "authentication_failed"
                    or frame.get("error_status") == 401
                    or frame.get("category") == "authentication_failed"):
                self.saw_auth_failure = True

        if ftype == "compact_boundary":
            self.compact_boundaries += 1

        if ftype == "result":
            self.result_subtype = frame.get("subtype")
            self.result_is_error = frame.get("is_error")
            self.terminal_reason = frame.get("terminal_reason")
            if "result" in frame:
                self.result_text = frame.get("result")
                if isinstance(self.result_text, str) and _MODEL_NOT_FOUND_RE.search(self.result_text):
                    self.saw_model_not_found = True
            self.usage = frame.get("usage")
            self.total_cost_usd = frame.get("total_cost_usd")
            self.duration_ms = frame.get("duration_ms")
            self.num_turns = frame.get("num_turns")


def classify(state: RunState, exit_code=None, elapsed_s=None, diff_is_empty=None) -> str:
    """Apply RELIABILITY_SPEC.md §C/§D to an accumulated RunState.

    `exit_code` is the child process's raw exit code (None if unknown). §D's exit-code
    cross-check is mandatory: SUCCESS is only legal at exit 0; absent a result frame,
    exit code IS the primary discriminator between KILLED(143)/OOM(137)/other.

    `elapsed_s` (wall-clock seconds) and `diff_is_empty` (worktree git diff) are
    optional, measured by the caller (fworker.py), not derived from stream frames —
    unlike everything else classify() reads. Callers that don't have them (unit tests,
    run_fire_child) simply omit them and get the pre-wq#1499 fallback verdicts below.
    """
    # §C9 SUCCESS-BUT-STUCK: a watchdog kill AFTER a real terminal result already
    # arrived is fworker forcing a stuck process to exit, not a stall — the result
    # frame is still the evidence of record. `state.hung` only means HANG/STALL (§C8)
    # when no result frame was ever observed; fworker.run_child sets a distinct
    # killed_reason ("post_result_stuck", never "inactivity") once a result has landed,
    # precisely so this check has something to key off.
    if state.hung and state.result_subtype is None:
        return HANG

    subtype = state.result_subtype
    is_error = state.result_is_error
    terminal_reason = state.terminal_reason

    # §C3/§D: turn_setup_failed is a launch failure BEFORE model work — never conflate
    # with a genuine turn ceiling (max_turns), regardless of whatever subtype/is_error
    # shape rides alongside it.
    if terminal_reason == "turn_setup_failed":
        return LAUNCH_FAILURE

    # wq#1499: a result frame this thin — <=1 real turn, wall-clock under 10s, a
    # nonzero exit, and the worktree shows no diff — is a launch-class fault the CLI
    # happened to still wrap in a success-shaped (or otherwise unrecognized) result
    # frame, indistinguishable in cause from turn_setup_failed above: no real model
    # work ever ran (live evidence: wq869/#879 attempts, #983/#1122, #1350 — every
    # one elapsed_s 1-2, turns=1, exit_code=1, empty git diff). Only overrides the
    # two fallback verdicts below (never TURNS/LIMIT/AUTH/BUDGET/MODEL_NOT_FOUND,
    # which have sharper evidence of their own) and only fires when the caller
    # actually measured both signals — an unmeasured None never counts as "empty".
    thin_no_diff_fault = (
        exit_code not in (None, 0)
        and state.turns <= 1
        and elapsed_s is not None and elapsed_s < 10
        and diff_is_empty is True
    )

    # §C1 SUCCESS — result:success + is_error:false + terminal_reason:completed (or
    # absent, pre-terminal_reason CLI versions carry no such field at all). Cross-check
    # exit code (§D): a success frame paired with a non-zero exit is a disagreement.
    if subtype == "success" and is_error is False and terminal_reason in (None, "completed"):
        if exit_code not in (None, 0):
            return LAUNCH_FAILURE if thin_no_diff_fault else INDETERMINATE
        if state.saw_refusal_no_fallback:
            return REFUSED
        # §C12 DEGRADED-SUCCESS (#7124): a genuine success frame whose `result` text
        # came back the empty string — read artifacts/transcript rather than trust it.
        if state.result_text == "":
            return DEGRADED_SUCCESS
        return SUCCESS

    # §C2 TURNS — needs evidence: an actual result frame AND >=1 real assistant turn.
    # Never legal without a result frame (§D) — a supervisor backstop SIGTERM with no
    # result frame falls through to the no-result branch below (KILLED/OOM/etc), not
    # here.
    if (subtype == "error_max_turns" or terminal_reason == "max_turns") and state.turns >= 1:
        return TURNS

    # §C4 LIMIT
    if state.saw_limit:
        return LIMIT
    # §C11 AUTH: checked after LIMIT (a rate-limited retry storm can also carry stray
    # 401s from an expired-mid-backoff token; the rate-limit read is the sharper one).
    if state.saw_auth_failure:
        return AUTH
    if subtype == "error_max_budget_usd":
        return BUDGET

    # wq#1442: a bad --model id/alias rejected before any real model turn ran
    # (turns <= 1: the CLI's own preflight message is itself the only "turn").
    # Checked after LIMIT/AUTH/BUDGET (a real one of those wins if it also
    # happens to co-occur) but before the generic catch-all below, so this
    # harness-configuration shape never gets bucketed as an ordinary CRASH.
    if state.saw_model_not_found and state.turns <= 1:
        return MODEL_NOT_FOUND

    if subtype is not None:
        # A result frame arrived but matched none of the shapes above — still a coarse
        # catch-all (wq#648/S3 hardened the NDJSON reader, not this bucket's boundary),
        # unless it's the wq#1499 thin/no-diff shape computed above.
        return LAUNCH_FAILURE if thin_no_diff_fault else CRASH

    # No result frame at all past this point (§C: "no result frame can NEVER be
    # SUCCESS" -> KILLED/OOM/LAUNCH_FAILURE/INDETERMINATE only).
    if not state.saw_init:
        return LAUNCH_FAILURE
    if exit_code == 143:
        return KILLED
    if exit_code == 137:
        return OOM
    # §C7 INDETERMINATE: last frame task_notification/task_updated/assistant with no
    # result -> never success (the #39632 drain-death signature, exit 0 with the
    # result frame dropped). Any other exit code with no result frame is an
    # exit-code/frame disagreement (§D) — fail toward INDETERMINATE too, never guess.
    return INDETERMINATE
