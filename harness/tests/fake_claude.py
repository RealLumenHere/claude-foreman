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

    # T4 (wq#618): dump the subset of env the local-rung gateway wiring cares about,
    # so integration tests can assert on the CHILD's actual env rather than build_env's
    # return value alone — proves the vars really reach the spawned process.
    env_dump_path = os.environ.get("FAKE_CLAUDE_ENV_DUMP")
    if env_dump_path:
        keys = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
                "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
                # wq#954: the Bash output cap moved to this env var (fworker.py
                # build_env()); dump it too or a test can never prove it reached
                # the child, only that build_env()'s return value carried it.
                "BASH_MAX_OUTPUT_LENGTH")
        with open(env_dump_path, "w") as f:
            json.dump({k: os.environ[k] for k in keys if k in os.environ}, f)

    # shim 3 (RELIABILITY_SPEC.md §F): instant death BEFORE the init frame — no
    # stream-json at all, no hook lifecycle. LAUNCH_FAILURE evidence is precisely the
    # absence of an init frame, so this must exit before emitting one.
    if scenario == "no_init_instant_exit":
        sys.exit(2)

    fire_lifecycle("start", session_id, effort)

    emit({"type": "system", "subtype": "init", "session_id": session_id, "model": model,
          "permissionMode": "acceptEdits"})

    # shim 6/7: init observed, then the process dies with no result frame at all —
    # mirrors a SIGTERM (143) or OOM SIGKILL (137) landing mid-turn. flush stdout
    # first: emit() is already flush=True per line, but os._exit skips the normal
    # interpreter teardown so nothing buffered downstream gets lost either way.
    if scenario == "sigterm_no_result":
        os._exit(143)
    if scenario == "oom_no_result":
        os._exit(137)

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
            # wq#759 evidence gate (real `wq` binary, not stubbed here): `done` refuses
            # notes that don't name a checkable artifact. README.md is the one file the
            # scratch repo is guaranteed to have (make_scratch_repo commits it) and it's
            # present in this worker's own worktree cwd, so it resolves as a real path
            # candidate — same shape a real worker's notes would need, no git-commit
            # plumbing required just to exercise the SUCCESS lifecycle path.
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes",
                             "fake worker done, see README.md"],
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
                # wq#759 evidence gate (see the "success" scenario above for detail) —
                # same fix, same reason: README.md is a real path in this worker's cwd.
                subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes",
                                 "resumed after limit, see README.md"],
                                capture_output=True, text=True)
            emit({"type": "result", "subtype": "success", "is_error": False})
        else:
            emit({"type": "rate_limit_event", "status": "rejected", "rateLimitType": "five_hour",
                  "resetsAt": time.time() + 2, "utilization": 1.0})
            emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "errors": ["usage limit reached"]})

    elif scenario == "turn_ceiling":
        # shim 2: real turns then the CLI's own --max-turns enforcement (§A: NOT
        # dropped in 2.1.233 — errors on ceiling with a real result:error_max_turns
        # frame, PROBE_RESULTS.md live-confirmed). A handful of real turns, well under
        # any archetype's ceiling, so this is unambiguously "the CLI stopped us", not
        # the supervisor's backstop.
        for _ in range(3):
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}})
            rc = fire_hook("Bash", {"command": "git status"}, session_id, effort)
            if rc != 0:
                emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                      "errors": ["hook blocked expected-allow command"]})
                fire_lifecycle("stop", session_id, effort)
                sys.exit(1)
        emit({"type": "result", "subtype": "error_max_turns", "is_error": True,
              "errors": ["Reached maximum number of turns (3)"]})
        fire_lifecycle("stop", session_id, effort)
        fire_lifecycle("end", session_id, effort)
        sys.exit(1)

    elif scenario == "turn_setup_failed":
        # shim 4: a launch failure BEFORE any model work — dead-turn terminal_reason
        # per RELIABILITY_SPEC.md §A/§C3, never a genuine turn ceiling.
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "terminal_reason": "turn_setup_failed",
              "errors": ["turn setup failed before model work began"]})
        fire_lifecycle("stop", session_id, effort)
        fire_lifecycle("end", session_id, effort)
        sys.exit(1)

    elif scenario == "task_notification_last":
        # shim 8: the #39632 drain-death race — task_notification/task_updated lands
        # as the LAST frame, the result frame is dropped, and the process still exits
        # 0. No wq done call: a real worker in this state never got to report back.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        emit({"type": "task_notification", "task_id": "bg-1", "status": "completed"})
        fire_lifecycle("stop", session_id, effort)
        fire_lifecycle("end", session_id, effort)
        sys.exit(0)

    elif scenario == "mid_task_silence":
        # shim 9 (§C8 STALL): a real turn, then silence with the process still alive —
        # no more frames ever. The test drives fworker with a short --frame-gap-timeout
        # so the watchdog fires well inside the test's own subprocess timeout.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SILENCE_S", "5")))

    elif scenario == "success_then_hang":
        # shim 10 (§C9 SUCCESS-BUT-STUCK, #25629): a real worker finishes AND calls wq
        # done itself (same as the "success" scenario), then the process just never
        # exits. fworker's post-result grace window forces the kill; the result frame
        # already observed is still the evidence of record (SUCCESS, not HANG/KILLED).
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        if ticket_id:
            # wq#759 evidence gate (see the "success" scenario above for detail).
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes",
                             "fake worker done, see README.md"],
                            capture_output=True, text=True)
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "done"})
        time.sleep(float(os.environ.get("FAKE_CLAUDE_STUCK_S", "3600")))

    elif scenario == "success_false_complete":
        # shim 11 (§C10 FALSE-COMPLETE): a clean result:success frame, but the worker
        # never actually closed the ticket (no wq done) and landed no commit — the
        # acceptance-criteria-unmet case. No artifact exists to verify against, so
        # fworker must not trust this self-report.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "done, I think"}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "all done"})

    elif scenario == "success_legit_block":
        # wq#749: a worker that does exactly what worker.md tells it to do on an
        # unworkable ticket — call `wq block`, print BLOCKED as the last line, exit
        # 0. classify() sees an ordinary result:success frame; fworker must read the
        # ticket's own "blocked" status and the BLOCKED sentinel together and treat
        # this as terminal, not FALSE_COMPLETE.
        emit({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "ticket needs a Frank decision, blocking"}]}})
        if ticket_id:
            subprocess.run([WQ_BIN, "block", str(ticket_id), "WORK_BLOCKED: needs Frank"],
                            capture_output=True, text=True)
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "blocking, needs Frank's call\nBLOCKED"})

    elif scenario == "success_unconfirmed_block_sentinel":
        # wq#749 adversarial case: the sentinel claims BLOCKED but the worker never
        # actually called `wq block` — the ticket status doesn't corroborate it, so
        # this must still fall through to the ordinary FALSE_COMPLETE path rather
        # than being trusted on the sentinel text alone.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": "BLOCKED"})

    elif scenario == "success_claim_error_sentinel":
        # wq#749: the worker discovers mid-session its claim raced (e.g. lease
        # stolen) and reports CLAIM_ERROR as instructed rather than doing the work
        # under a stale claim. exit 0, ordinary result:success frame.
        emit({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "claim looks stale, bailing"}]}})
        emit({"type": "result", "subtype": "success", "is_error": False,
              "result": "claim race detected\nCLAIM_ERROR"})

    elif scenario == "auth_401":
        # shim 12 (§C11 AUTH): system/api_retry authentication_failed + a bare 401.
        emit({"type": "api_retry", "attempt": 1, "error_status": 401,
              "error": "authentication_failed"})
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "errors": ["authentication_failed"]})

    elif scenario == "success_empty_result_text":
        # shim 13 (§C12 DEGRADED-SUCCESS, #7124): result:success but the `result` text
        # field is the empty string. No wq done call either, so this also exercises the
        # same artifact-verification fallthrough as FALSE-COMPLETE once classified.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        emit({"type": "result", "subtype": "success", "is_error": False, "result": ""})

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

    elif scenario == "buffered_burst":
        # shim 14 (RELIABILITY_SPEC.md §C13 NOT-A-FAILURE, #25670): stdout is
        # block-buffered when piped, so frames can sit unflushed for a few seconds
        # then arrive as a burst — indistinguishable, from the reader's side, from a
        # real silent gap of the same length. As long as that gap stays under the
        # frame-gap watchdog's T_gap, this must complete SUCCESS, never HANG. The test
        # drives a gap shorter than --frame-gap-timeout to prove no false stall fires.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        time.sleep(float(os.environ.get("FAKE_CLAUDE_BURST_DELAY_S", "1")))
        for i in range(5):
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": f"burst {i}"}]}})
        if ticket_id:
            # wq#759 evidence gate (see the "success" scenario above for detail).
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes",
                             "fake worker done, see README.md"],
                            capture_output=True, text=True)
        emit({"type": "result", "subtype": "success", "is_error": False})

    elif scenario == "partial_line_split":
        # shim 15: NDJSON reassembly — one frame's bytes written in two separate
        # writes/flushes with no trailing newline on the first, so the reader must
        # carry the partial line forward and only parse once the newline lands. Bypasses
        # emit() (always a single flush=True print) to control the raw byte-level split.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        frame = json.dumps({"type": "assistant",
                             "message": {"content": [{"type": "text", "text": "split frame"}]}})
        half = len(frame) // 2
        sys.stdout.write(frame[:half])
        sys.stdout.flush()
        time.sleep(0.2)
        sys.stdout.write(frame[half:] + "\n")
        sys.stdout.flush()
        if ticket_id:
            # wq#759 evidence gate (see the "success" scenario above for detail).
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes",
                             "fake worker done, see README.md"],
                            capture_output=True, text=True)
        emit({"type": "result", "subtype": "success", "is_error": False})

    elif scenario == "unknown_frame_type":
        # shim 16: a frame type this harness has never seen (future CLI version) must
        # fall through untouched — no crash, no misclassification — while the rest of
        # the stream still classifies normally.
        emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]}})
        emit({"type": "some_future_frame_type_v99", "wat": "?", "nested": {"a": [1, 2, 3]}})
        if ticket_id:
            # wq#759 evidence gate (see the "success" scenario above for detail).
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes",
                             "fake worker done, see README.md"],
                            capture_output=True, text=True)
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
            # wq#759 evidence gate (see the "success" scenario above for detail).
            subprocess.run([WQ_BIN, "done", str(ticket_id), "--notes",
                             "fake worker done, see README.md"],
                            capture_output=True, text=True)
        emit({"type": "result", "subtype": "success", "is_error": False,
              "total_cost_usd": 0.002, "num_turns": 10})

    fire_lifecycle("stop", session_id, effort)
    fire_lifecycle("end", session_id, effort)


if __name__ == "__main__":
    main()
