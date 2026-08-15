"""Stream-json event classifier — RELIABILITY_SPEC.md §C/§D verdict engine (S1, wq#646).

RELIABILITY_SPEC.md is frozen law for this harness (cross-validated by wq#645's live
findings). §D's governing invariant: no verdict without evidence. A verdict that would
otherwise rest on absent evidence must fall to the most conservative reading — never
guessed as SUCCESS, never silently folded into a generic CRASH bucket once the spec
gives us a sharper answer.

S1 scope (this file): the 7 canonical outcomes exercised by RELIABILITY_SPEC.md §F
shim tests 1-8 — SUCCESS, TURNS, LAUNCH_FAILURE, LIMIT, KILLED, OOM, INDETERMINATE.
§C's remaining outcomes (STALL, SUCCESS-BUT-STUCK, FALSE-COMPLETE, AUTH,
DEGRADED-SUCCESS, NOT-A-FAILURE — shim tests 9-16) are wq#647/wq#648 (S2/S3) work;
REFUSED/BUDGET/CRASH/HANG here are pre-existing extensions kept as coarse catch-alls
for shapes S1 doesn't yet have a sharper spec-mandated answer for.
"""

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
        self.compact_boundaries = 0
        self.result_subtype = None
        self.result_is_error = None
        self.terminal_reason = None
        self.last_frame_type = None
        self.last_event_ts = None
        self.hung = False
        self.turns = 0
        self.turns_exceeded = False

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

        # api_retry (and any other frame) can carry error-category "rate_limit" at the
        # top level, or a bare 429, per RELIABILITY_SPEC.md §B.
        if frame.get("category") == "rate_limit" or frame.get("error_category") == "rate_limit":
            self.saw_limit = True
        if frame.get("error_status") == 429 or frame.get("api_error_status") == 429:
            self.saw_limit = True

        if ftype in ("model_refusal_no_fallback",):
            self.saw_refusal_no_fallback = True

        if ftype == "compact_boundary":
            self.compact_boundaries += 1

        if ftype == "result":
            self.result_subtype = frame.get("subtype")
            self.result_is_error = frame.get("is_error")
            self.terminal_reason = frame.get("terminal_reason")


def classify(state: RunState, exit_code=None) -> str:
    """Apply RELIABILITY_SPEC.md §C/§D to an accumulated RunState.

    `exit_code` is the child process's raw exit code (None if unknown). §D's exit-code
    cross-check is mandatory: SUCCESS is only legal at exit 0; absent a result frame,
    exit code IS the primary discriminator between KILLED(143)/OOM(137)/other.
    """
    if state.hung:
        return HANG

    subtype = state.result_subtype
    is_error = state.result_is_error
    terminal_reason = state.terminal_reason

    # §C3/§D: turn_setup_failed is a launch failure BEFORE model work — never conflate
    # with a genuine turn ceiling (max_turns), regardless of whatever subtype/is_error
    # shape rides alongside it.
    if terminal_reason == "turn_setup_failed":
        return LAUNCH_FAILURE

    # §C1 SUCCESS — result:success + is_error:false + terminal_reason:completed (or
    # absent, pre-terminal_reason CLI versions carry no such field at all). Cross-check
    # exit code (§D): a success frame paired with a non-zero exit is a disagreement.
    if subtype == "success" and is_error is False and terminal_reason in (None, "completed"):
        if exit_code not in (None, 0):
            return INDETERMINATE
        if state.saw_refusal_no_fallback:
            return REFUSED
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
    if subtype == "error_max_budget_usd":
        return BUDGET

    if subtype is not None:
        # A result frame arrived but matched none of the shapes above. S1 keeps this
        # bucket coarse (CRASH); wq#647/wq#648 split AUTH / DEGRADED-SUCCESS /
        # FALSE-COMPLETE out of it per §C11/§C12/§C10.
        return CRASH

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
