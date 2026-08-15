#!/usr/bin/env python3
"""fake claude -p shim for zero-quota fworker integration tests.

Accepts the same flag shape fworker.py invokes (`-p PROMPT --model M --effort E
--settings F --permission-mode M --add-dir D... --output-format stream-json --verbose
--include-hook-events [--resume SID]`), ignores the ones it doesn't need, and emits a
canned NDJSON stream-json fixture selected by $FAKE_CLAUDE_SCENARIO. Scenarios also
drive guard.sh for real (so a PreToolUse block is a genuine subprocess call, not a
stand-in) and, for the "success" scenario, call wq done itself — mirroring what a real
worker does — so fworker's reconcile() sees a genuinely closed ticket.
"""
import json
import os
import subprocess
import sys
import time

GUARD_SH = os.environ.get("FAKE_CLAUDE_GUARD_SH", "/home/lumen/foreman/harness/guard.sh")
WQ_BIN = os.environ.get("FAKE_CLAUDE_WQ_BIN", "/home/lumen/bin/wq")


def emit(frame):
    print(json.dumps(frame), flush=True)


def fire_hook(tool_name, tool_input, session_id, effort):
    payload = json.dumps({
        "tool_name": tool_name, "tool_input": tool_input,
        "session_id": session_id, "cwd": os.getcwd(),
        "effort": {"level": effort},
    })
    env = dict(os.environ)
    p = subprocess.run([GUARD_SH], input=payload, capture_output=True, text=True, env=env)
    return p.returncode


def fire_lifecycle(phase, session_id, effort):
    payload = json.dumps({"session_id": session_id, "cwd": os.getcwd(),
                           "effort": {"level": effort}})
    env = dict(os.environ)
    subprocess.run([GUARD_SH, "--lifecycle", phase], input=payload,
                    capture_output=True, text=True, env=env)


def main():
    argv = sys.argv[1:]
    model = effort = ticket_id = None
    resume = None
    for i, a in enumerate(argv):
        if a == "--model" and i + 1 < len(argv):
            model = argv[i + 1]
        if a == "--effort" and i + 1 < len(argv):
            effort = argv[i + 1]
        if a == "--resume" and i + 1 < len(argv):
            resume = argv[i + 1]

    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "success")
    ticket_id = os.environ.get("FAKE_CLAUDE_TICKET")
    session_id = resume or f"fake-session-{scenario}-{int(time.time())}"

    fire_lifecycle("start", session_id, effort)

    emit({"type": "system", "subtype": "init", "session_id": session_id, "model": model,
          "permissionMode": "acceptEdits"})

    if scenario == "success":
        # a real worker does some work then calls wq done itself.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        emit({"type": "assistant",
              "message": {"content": [{"type": "tool_use", "name": "Bash",
                                        "input": {"command": "git status"}}]}})
        rc = fire_hook("Bash", {"command": "git status"}, session_id, effort)
        if rc != 0:
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "errors": ["hook blocked expected-allow command"]})
            fire_lifecycle("stop", session_id, effort)
            sys.exit(1)
        if ticket_id:
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes", "fake worker done"],
                            capture_output=True, text=True)
        emit({"type": "result", "subtype": "success", "is_error": False,
              "total_cost_usd": 0.001, "num_turns": 2})

    elif scenario == "denied_verb_blocked":
        rc = fire_hook("Bash", {"command": "docker compose down --remove-orphans"},
                        session_id, effort)
        emit({"type": "assistant", "message": {"content": [
            {"type": "tool_result", "is_error": rc != 0}]}})
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "errors": [f"tool blocked, hook_exit={rc}"]})

    elif scenario == "rejected_limit":
        # resets far beyond RESUME_WAIT_MAX_S so the test exercises fworker's
        # release-and-exit LIMIT path deterministically rather than its sleep-then-
        # resume path (which would otherwise loop this fake scenario forever, since
        # it always reports "rejected" again on resume).
        emit({"type": "rate_limit_event", "status": "rejected", "rateLimitType": "five_hour",
              "resetsAt": time.time() + 999999, "utilization": 1.0})
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "errors": ["usage limit reached"]})

    elif scenario == "rejected_limit_near":
        # first pass (no --resume) reports a near-future reset so fworker sleeps
        # briefly then resumes; the resumed pass (--resume present) succeeds, closing
        # the ticket. Exercises the resume-after-LIMIT branch design §7 describes.
        if resume:
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "resumed"}]}})
            if ticket_id:
                subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes", "resumed after limit"],
                                capture_output=True, text=True)
            emit({"type": "result", "subtype": "success", "is_error": False})
        else:
            emit({"type": "rate_limit_event", "status": "rejected", "rateLimitType": "five_hour",
                  "resetsAt": time.time() + 2, "utilization": 1.0})
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "errors": ["usage limit reached"]})

    elif scenario == "crash":
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "oops"}]}})
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "errors": ["internal error"]})

    elif scenario == "hang":
        time.sleep(3600)

    elif scenario == "turns_flood":
        for _ in range(200):
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}})
            time.sleep(0.01)
        emit({"type": "result", "subtype": "success", "is_error": False})

    elif scenario == "thinking_paired_turns":
        # wq#645 regression fixture: mirrors the real 2.1.233 shape (thinking block as
        # its own assistant frame ahead of the tool_use/text frame for the same turn).
        # 10 real turns = 20 raw assistant frames; mechanical-edit's max_turns=15 means
        # the pre-fix double-count kills this at raw frame 15 (~7.5 real turns, TURNS);
        # post-fix it completes all 10 real turns and succeeds.
        for i in range(10):
            emit({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": f"step {i}"}]}})
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}})
            rc = fire_hook("Bash", {"command": "git status"}, session_id, effort)
            if rc != 0:
                emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "errors": ["hook blocked expected-allow command"]})
                fire_lifecycle("stop", session_id, effort)
                sys.exit(1)
        if ticket_id:
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes", "fake worker done"],
                            capture_output=True, text=True)
        emit({"type": "result", "subtype": "success", "is_error": False,
              "total_cost_usd": 0.002, "num_turns": 10})

    fire_lifecycle("stop", session_id, effort)
    fire_lifecycle("end", session_id, effort)


if __name__ == "__main__":
    main()
