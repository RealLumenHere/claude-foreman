"""Stream-json event classifier — CLAUDE_CLI_INTERNALS.md §E, verbatim.

Fail-closed: an attempt fworker cannot positively classify as SUCCESS/REFUSED/
TURNS/BUDGET/CRASH falls to LIMIT (pause the tier, never burn the recovering
window on a guess).
"""

SUCCESS = "SUCCESS"
REFUSED = "REFUSED"
TURNS = "TURNS"
BUDGET = "BUDGET"
LIMIT = "LIMIT"
CRASH = "CRASH"
HANG = "HANG"


class RunState:
    """Accumulates the signals classify() needs while a child's stdout is parsed
    line by line. One instance per attempt."""

    def __init__(self):
        self.session_id = None
        self.model = None
        self.permission_mode = None
        self.saw_limit = False
        self.latest_resets_at = None
        self.saw_refusal_no_fallback = False
        self.compact_boundaries = 0
        self.result_subtype = None
        self.result_is_error = None
        self.last_event_ts = None
        self.hung = False
        self.reaped = False
        self.turns = 0
        self.turns_exceeded = False

    def observe(self, frame):
        """Feed one parsed stream-json frame (dict). Returns nothing; mutates state."""
        ftype = frame.get("type")

        if ftype == "system" and frame.get("subtype") == "init":
            self.session_id = frame.get("session_id")
            self.model = frame.get("model")
            self.permission_mode = frame.get("permissionMode") or frame.get("permission_mode")

        # claude 2.1.233 dropped the --max-turns CLI flag present in 2.1.220 (the version
        # BUILD_PLAN/design assumed) — `strings` on the installed binary still carries
        # maxTurns/resolvedMaxTurns as an --agents frontmatter key only, not a top-level
        # print flag. fworker enforces the turn ceiling itself, counted here; the
        # supervisor SIGTERMs on ceiling (fworker.py).
        #
        # wq#645: one agentic-loop turn is NOT one "assistant" frame. With extended
        # thinking on, the CLI emits a *separate* assistant frame whose content is
        # solely a "thinking" block, then a second assistant frame with the real
        # tool_use/text block — both frames share one stop_reason. Counting every
        # assistant frame double-counted real turns 2x, so fworker's self-enforced
        # ceiling fired at ~half the intended max_turns and misclassified genuinely
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
        # top level per CLAUDE_CLI_INTERNALS §E (f_l enum includes "rate_limit").
        if frame.get("category") == "rate_limit" or frame.get("error_category") == "rate_limit":
            self.saw_limit = True

        if ftype in ("model_refusal_no_fallback",):
            self.saw_refusal_no_fallback = True

        if ftype == "compact_boundary":
            self.compact_boundaries += 1

        if ftype == "result":
            self.result_subtype = frame.get("subtype")
            self.result_is_error = frame.get("is_error")


def classify(state: RunState) -> str:
    """Apply the §E decision table to an accumulated RunState."""
    if state.hung:
        return HANG
    if state.turns_exceeded:
        return TURNS
    if state.reaped:
        return CRASH

    subtype = state.result_subtype
    is_error = state.result_is_error

    if subtype == "success" and is_error is False:
        if state.saw_refusal_no_fallback:
            return REFUSED
        return SUCCESS

    if subtype == "error_max_turns":
        return TURNS
    if subtype == "error_max_budget_usd":
        return BUDGET

    if is_error or subtype is None:
        if state.saw_limit:
            return LIMIT
        if subtype is None:
            # no result frame at all (killed/crashed before one arrived) — CRASH unless
            # a rejected rate_limit_event already told us it was actually the wall.
            return CRASH
        return CRASH

    # Any other shape we did not anticipate: fail closed to LIMIT rather than guess CRASH,
    # per §E ("ambiguous => LIMIT, pause, never burn the recovering window").
    return LIMIT
