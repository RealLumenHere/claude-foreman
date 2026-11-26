#!/usr/bin/env python3
"""wq#5144 — worker stop-code contract (wq#5041 gap 6, CodeAF `codeaf do` exit codes).

A worker ends its final message with an explicit stop code, so fworker routes the exit
on a number the worker chose instead of inferring intent from prose and frame shape:

    STOP: 0   done          landed + `wq done` ran        -> harness verifies (never trusted)
    STOP: 1   cannot run    the ticket cannot proceed     -> blocked, no retry
    STOP: 2   unfinished    ran, more work remains        -> resume / retry, never closed
    STOP: 3   limit         a ceiling of the worker's own -> limit path (LIMIT_HIT block)
    STOP: 4   needs answer  only Frank can decide         -> the ```foreman-park``` card

Trust rule (RELIABILITY_SPEC.md §C10): a stop code may only make the harness MORE
conservative. Code 0 is still a claim the evidence replay, artifact check and exit gate
must verify. Codes 1-4 say "do not close this", which the harness can always honour.
A harness-observed failure (KILLED, OOM, TURNS, CRASH ...) always beats the worker's
code: the code is only read on a clean exit (SUCCESS / DEGRADED_SUCCESS).

Only sessions whose prompt carried PROTOCOL_MARKER (prompts/worker.md) are routed on
it; everything else is recorded. A missing or out-of-range code falls back to the
pre-wq#5144 inference unchanged.

Rollback: FOREMAN_STOP_CODE=off (record nothing, route nothing) | shadow (record and
log, route nothing) | enforce (default).

stdlib only.
"""
import os
import re

DONE, CANT_RUN, UNFINISHED, LIMIT, NEEDS_ANSWER = 0, 1, 2, 3, 4
NAMES = {DONE: "done", CANT_RUN: "cant_run", UNFINISHED: "unfinished",
         LIMIT: "limit", NEEDS_ANSWER: "needs_answer"}
ROUTES = {DONE: "verify", CANT_RUN: "block", UNFINISHED: "resume",
          LIMIT: "limit", NEEDS_ANSWER: "park"}
MODE_ENV = "FOREMAN_STOP_CODE"
PROTOCOL_MARKER = "wq#5144 stop-code contract"

# What each code expects on the two lines after it (EXIT_SIGNAL, last-line sentinel).
# None = no expectation. Used only to name contradictions in the attempt record.
_EXPECT = {
    DONE: (True, "TICKET_CLOSED"),
    CANT_RUN: (False, "BLOCKED"),
    UNFINISHED: (False, None),
    LIMIT: (False, None),
    NEEDS_ANSWER: (False, "PARKED"),
}

# Same tolerance as exit_gate._SIGNAL_RE: a line of its own, markdown decoration ok.
_STOP_RE = re.compile(r"^[ \t>*\-`]*STOP[ \t]*[:=][ \t]*`?(\d{1,2})\b`?[ \t.*`]*$",
                      re.MULTILINE)


def mode():
    m = (os.environ.get(MODE_ENV) or "enforce").strip().lower()
    return m if m in ("enforce", "shadow", "off") else "enforce"


def parse(text):
    """(code, raw) from the LAST `STOP: <n>` line of the final message.
    code is None when there is no line or the number is outside 0-4; raw keeps the
    number as written (None when there was no line) so an out-of-range code is visible
    in the record instead of silently reading as 'missing'."""
    if not isinstance(text, str):
        return None, None
    hits = _STOP_RE.findall(text)
    if not hits:
        return None, None
    raw = hits[-1]
    n = int(raw)
    return (n if n in NAMES else None), raw


def last_line(text):
    if not isinstance(text, str):
        return None
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return line.strip()
    return None


def contradictions(code, exit_signal, text, has_card=False):
    """Human-readable disagreements between the code and the lines/card around it.
    Recorded, never routed on: the conservative reading always wins downstream."""
    if code is None:
        return []
    want_signal, want_last = _EXPECT[code]
    out = []
    if exit_signal is not None and exit_signal != want_signal:
        out.append(f"STOP: {code} with EXIT_SIGNAL: {str(exit_signal).lower()}")
    last = last_line(text)
    if want_last and last != want_last:
        out.append(f"STOP: {code} but last line is {last!r}, expected {want_last}")
    if code == NEEDS_ANSWER and not has_card:
        out.append("STOP: 4 without a foreman-park card")
    if code != NEEDS_ANSWER and has_card:
        out.append(f"foreman-park card with STOP: {code}")
    return out


def record(text, exit_signal=None, has_card=False, enforced=False):
    """The attempt-record block: what the worker said and what the harness made of it."""
    code, raw = parse(text)
    return {
        "code": code,
        "raw": raw,
        "name": NAMES.get(code),
        "route": ROUTES.get(code) if enforced and code is not None else None,
        "mode": mode(),
        "enforced": bool(enforced),
        "contradictions": contradictions(code, exit_signal, text, has_card),
    }


def summary_line(text, limit=200):
    """First non-empty line of the final message that is not one of the closing lines —
    the worker's own one-line account, used as the block reason / Frank prompt head."""
    if not isinstance(text, str):
        return ""
    closing = re.compile(r"^(STOP|EXIT_SIGNAL)\s*[:=]|^(TICKET_CLOSED|BLOCKED|CLAIM_ERROR|"
                         r"PARKED)$")
    for line in text.strip().splitlines():
        s = line.strip().strip("`*> -")
        if s and not closing.match(s):
            return s[:limit]
    return ""


def block_reason(text):
    head = summary_line(text) or "no reason given in the final message"
    return f"worker STOP: 1 (cannot run, wq#5144): {head}"


def card_missing_text():
    """The one corrective turn a STOP: 4 without a card gets (same attempt)."""
    return ("STOP CODE 4 WITHOUT A DECISION CARD: you ended with `STOP: 4` (needs an "
            "answer) but sent no ```foreman-park block, so there is no question to put to "
            "Frank. Re-send your final message with the card — one JSON object "
            '{"ask": {"title": "...", "text": "...", "options": ["...", "..."], '
            '"recommend": "<one of options>"}} — followed by the lines `STOP: 4`, '
            "`EXIT_SIGNAL: false` and `PARKED`. If you no longer need a decision, carry on "
            "with the ticket and end with the stop code that is true.")


def frank_prompt(ticket_id, text):
    head = summary_line(text, limit=400) or "(no summary line)"
    return (f"wq#{ticket_id}: the worker stopped with STOP: 4 (needs an answer) and sent "
            f"no usable decision card after one request. Its own summary: {head}. "
            f"Options: (1) answer in the ticket notes and reopen it; (2) rewrite the "
            f"ticket so the decision is recorded up front; (3) retire the ticket.")
