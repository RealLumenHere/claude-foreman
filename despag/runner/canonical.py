"""wq#1806: the canonical frame schema IS Claude Code `stream-json` (system/init,
assistant, user, result) — PHASE0_WIRING_SPEC.md C.1. A backend normalizer maps its
own wire shape into this schema; for `claude_max` (despag/backends/claude_max.py) the
CLI already emits it, so normalization here is the identity plus two corrections that
HR's own `claude -p` reader (HR/runner/server.py:903-1033) found necessary in three
weeks of fleet evidence (PHASE0_WIRING_SPEC.md E.2):

1. `_strip_claude_error_text` — the CLI renders a transient provider error as an
   assistant TEXT block instead of a distinct frame type. Left alone, that text block
   satisfies "the turn produced output" and the run reads as a normal completion
   (112 of 1306 token-pool fleet attempts, FALSE_COMPLETE, PHASE0_WIRING_SPEC.md E.2).
2. `_norm_token_usage` — one usage shape across whatever field-name variant the CLI
   emits, with `input_tokens` always the FRESH (non-cached) count so a name drift can
   never zero out or double-count billing.
"""
from __future__ import annotations

import re

# HR/runner/server.py:900 — the CLI's own retry-banner text, batch and partial modes
# alike: "[API Error: ...]" or "API Error: ...".
_API_ERROR_RE = re.compile(r"^\s*\[?API Error:", re.IGNORECASE)


def _strip_claude_error_text(frame: dict, state: dict) -> dict | None:
    """Drop `API Error: ...` text blocks from an assistant message. Returns the frame
    with those blocks removed (None if nothing renderable remains), or the frame
    unchanged when it carries no such block. The dropped text is remembered on
    `state["_cli_error"]` so a turn that ends with no real answer can be judged by it
    (see `normalize` below, mirrors HR/runner/server.py:903-925)."""
    if frame.get("type") != "assistant":
        return frame
    content = (frame.get("message") or {}).get("content")
    if not isinstance(content, list):
        return frame
    for c in content:
        if isinstance(c, dict) and c.get("type") == "text" and _API_ERROR_RE.match(str(c.get("text") or "")):
            state["_cli_error"] = str(c.get("text") or "").strip().strip("[]").strip()[:400]
    kept = [c for c in content
            if not (isinstance(c, dict) and c.get("type") == "text"
                    and _API_ERROR_RE.match(str(c.get("text") or "")))]
    if len(kept) == len(content):
        return frame                      # no error block — untouched
    if not kept:
        return None                       # the message was ONLY the error line — drop it
    return {**frame, "message": {**(frame.get("message") or {}), "content": kept}}


def normalize(frame: dict, state: dict) -> list[dict]:
    """One raw claude `stream-json` frame -> zero or more canonical frames.

    `state` is one dict per turn, threaded through every call so the error-text scrub
    can remember what it stripped across frames within the same turn.
    """
    frame = _strip_claude_error_text(frame, state)
    if frame is None:
        return []
    if frame.get("type") == "result":
        # HR/runner/server.py:934-946 (PHASE0_WIRING_SPEC.md E.2): a turn whose only
        # "answer" is the CLI's own error banner is a failed turn with that banner as
        # its reason, not a success with strange output.
        answer = str(frame.get("result") or "")
        if _API_ERROR_RE.match(answer):
            state["_cli_error"] = answer.strip().strip("[]").strip()[:400]
            answer = ""
        if not answer.strip() and state.get("_cli_error") and not frame.get("is_error"):
            return [{**frame, "subtype": "error", "is_error": True, "result": state["_cli_error"]}]
    return [frame]


def _norm_token_usage(u: dict | None) -> dict:
    """One usage shape `{input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens}` from whatever field-name variant the CLI's `usage` object
    carries. `input_tokens` is always FRESH input only — cache reads are netted out so
    a name drift can never double- or zero-count billing (HR/runner/server.py:981-1033,
    PHASE0_WIRING_SPEC.md C.1)."""
    if not isinstance(u, dict):
        return {}

    def _pick(*names) -> int:
        for n in names:
            v = u.get(n)
            if isinstance(v, (int, float)) and v:
                return int(v)
        return 0

    inp = _pick("input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
    out = _pick("output_tokens", "outputTokens", "completion_tokens", "completionTokens")
    cache_read = _pick("cache_read_tokens", "cacheReadTokens",
                        "cache_read_input_tokens", "cacheReadInputTokens")
    cache_write = _pick("cache_write_tokens", "cacheWriteTokens",
                         "cache_creation_input_tokens", "cacheCreationInputTokens")
    fresh = max(inp - cache_read, 0) if cache_read else inp
    result = {"input_tokens": fresh, "output_tokens": out}
    if cache_read:
        result["cache_read_tokens"] = cache_read
    if cache_write:
        result["cache_write_tokens"] = cache_write
    return result


def _status_from_result(result_frame: dict | None, exit_code: int | None) -> str:
    """`success & !is_error` -> completed; `error_max_turns` -> incomplete(max_turns);
    else failed. No result frame at all -> the exit code decides (HR/runner/server.py:
    1163-1171). Callers layer cancelled/timeout/hang above this (turn.py Turn.status)."""
    if result_frame is not None:
        subtype = result_frame.get("subtype")
        if subtype == "success" and not result_frame.get("is_error"):
            return "completed"
        if subtype == "error_max_turns":
            return "incomplete"
        return "failed"
    return "completed" if exit_code == 0 else "failed"
