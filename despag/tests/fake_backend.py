#!/usr/bin/env python3
"""wq#1806: a stand-in for `claude -p --output-format stream-json` used only by
despag/tests/test_runner.py. Prints real Claude Code stream-json frames (one JSON
object per line, flushed immediately) at a controllable pace so the tests can exercise
despag.runner.turn.Turn's watchdogs deterministically, without ever spawning a real
`claude -p` process. Never imported — always run as a subprocess, exactly like the
real CLI would be.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def init_frame(session_id: str = "fake-session") -> dict:
    return {"type": "system", "subtype": "init", "session_id": session_id, "model": "fake-model"}


def assistant_text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def result_frame(*, subtype: str = "success", is_error: bool = False, result: str = "ok") -> dict:
    return {"type": "result", "subtype": subtype, "is_error": is_error, "result": result,
            "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 40}}


def rate_limit_event(resets_at: str) -> dict:
    """wq#1814 wire shape (classifier.py:201-210): `rate_limit_info` nested one level
    down, `status: rejected` is what promotes classifier.RunState.saw_limit."""
    return {"type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": resets_at}}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", required=True,
                    choices=["quick", "grandchild", "steady", "silent", "api_error",
                             "rate_limited"])
    p.add_argument("--count", type=int, default=1000)
    p.add_argument("--interval", type=float, default=0.05)
    p.add_argument("--sleep-seconds", type=float, default=300)
    args = p.parse_args()

    emit(init_frame())

    if args.mode == "quick":
        emit(assistant_text("ok"))
        emit(result_frame())
        return 0

    if args.mode == "grandchild":
        # No start_new_session here: this grandchild inherits THIS process's process
        # group, which is the one Turn._kill() killpg's — proving a group kill reaches
        # it even though Turn never spawned it directly (PHASE0_WIRING_SPEC.md E.1).
        child = subprocess.Popen([sys.executable, "-c",
                                   f"import time; time.sleep({args.sleep_seconds})"])
        emit({"type": "test_grandchild_pid", "pid": child.pid})
        for i in range(args.count):
            emit(assistant_text(f"working {i}"))
            time.sleep(args.interval)
        child.wait()
        emit(result_frame())
        return 0

    if args.mode == "steady":
        # Keeps emitting frames fast enough to never trip a frame-gap watchdog, for
        # long enough to trip a short wall-clock cap instead.
        for i in range(args.count):
            emit(assistant_text(f"tick {i}"))
            time.sleep(args.interval)
        emit(result_frame())
        return 0

    if args.mode == "silent":
        # One frame, then quiet — trips the frame-gap (inactivity) watchdog.
        time.sleep(args.sleep_seconds)
        emit(result_frame())
        return 0

    if args.mode == "rate_limited":
        # A mid-turn rejected rate_limit_event with no result frame ever following
        # it (classifier.py's LIMIT check — CL:358-360 — fires unconditionally on
        # saw_limit, ahead of the no-result-frame branch, so this alone is enough
        # for classify() to return LIMIT rather than SUCCESS/INDETERMINATE) — the
        # shape wq#1888's `lumen.limit` mid-stream event and
        # `lumen.run_state.saw_limit` sidecar both need to be exercised against
        # (PHASE2_WIRING_SPEC.md §B.6).
        emit(assistant_text("working"))
        emit(rate_limit_event("2026-09-12T13:00:00+00:00"))
        return 1

    if args.mode == "api_error":
        # The CLI's own transient-error banner rendered as assistant text, with no
        # real answer after it — canonical.normalize must rewrite the result frame
        # (PHASE0_WIRING_SPEC.md E.2).
        emit(assistant_text("API Error: 529 overloaded_error, retrying in 1s..."))
        emit(result_frame(result=""))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
