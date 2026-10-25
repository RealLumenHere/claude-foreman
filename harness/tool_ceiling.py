"""wq#4634: re-issue tool-ceiling-killed Bash calls via bin/watchdog, resume the session.

A `claude -p` child's Bash call can be killed by the harness/transport ceiling rather
than by the command itself (the kill mechanism family is the supergateway group reap /
Worker 70s abort, reports/wq3264_mcpw_commander_drops_rca.md; for a local child the
observable is the CLI's own Bash timeout). The model then sees an error tool_result and
either retries into the same ceiling or misdiagnoses a healthy command as broken.

This module gives fworker the recovery path (design: harness/TOOL_CEILING_REISSUE.md):

  1. CeilingTracker — fed the same stream-json frames LoopTracker sees; pairs each Bash
     tool_use with its tool_result and fires when the error text matches a known
     ceiling-kill class. run_child() stops the session (killed_reason="tool_ceiling").
  2. reissue() — re-runs the killed command under /home/lumen/bin/watchdog --no-send
     (daemonised setsid, so it survives every reaper class in the RCA), waits for its
     result.json (instant wake via a one-shot localhost --callback sink, polling as the
     fallback), and returns a verdict line.
  3. do_attempt() then resumes the SAME session (`claude -p --resume <session_id>`) with
     that verdict as the prompt — the wq#2138 steer channel: a -p child has no stdin, so
     the one message rides a --resume. Same attempt number; the ceiling kill is a
     harness event, never charged to the ticket.

Kill classes are evidence-driven: only signatures with a verified source ship built-in
(never guess an error string — the wq#2795 lesson generalises). New classes land via
FOREMAN_CEILING_KILL_PATTERNS once a real frame has been captured.
"""
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WATCHDOG_BIN = os.environ.get("FOREMAN_WATCHDOG_BIN", "/home/lumen/bin/watchdog")
# Re-issues per attempt. After the budget the tracker goes quiet and the session's own
# error handling / classify() judges the kill like any other failure.
REISSUE_MAX = int(os.environ.get("FOREMAN_CEILING_REISSUE_MAX", "2"))
# The re-run's own ceiling, minutes (watchdog --timeout). The original call's timeout is
# irrelevant — it was the ceiling that killed it.
REISSUE_TIMEOUT_MIN = float(os.environ.get("FOREMAN_CEILING_REISSUE_TIMEOUT_MIN", "30"))
# How long past watchdog's own timeout reissue() keeps waiting before concluding the
# watchdog itself is gone (it should have written a TIMEOUT result.json by then).
WAIT_SLACK_S = float(os.environ.get("FOREMAN_CEILING_WAIT_SLACK_S", "120"))

# Built-in kill classes. error_re is matched against the START of the tool_result error
# text (\A): the CLI puts its timeout banner first, and anchoring keeps a failing test
# suite that merely *prints* the phrase mid-output from being mistaken for a ceiling
# kill. Verified source for bash_timeout: code.claude.com/docs/en/hooks shows the
# PostToolUseFailure example "Command timed out after 2m 0s". No MCP-abort class ships
# yet — no captured frame to pin its text to (TOOL_CEILING_REISSUE.md §4).
BUILTIN_KILL_CLASSES = (
    {"class": "bash_timeout", "tool_re": r"\ABash\Z",
     "error_re": r"\ACommand timed out after "},
)
KILL_PATTERNS_ENV = "FOREMAN_CEILING_KILL_PATTERNS"

# Commands that are never blindly re-run: anything that lands/publishes state where a
# half-applied first try plus an automatic second try can double-apply (push, PR land,
# ticket verbs). Those get an advisory verdict instead — the session is told the call
# was ceiling-killed and must verify state itself.
NEVER_REISSUE_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:\S*/)?(?:git\s+push|lumen-pr\s+(?:commit|open|land)|"
    r"wq\s+(?:done|block|release))\b")


def kill_classes():
    """Built-ins plus any operator-supplied classes from the JSON file named by
    FOREMAN_CEILING_KILL_PATTERNS: [{"class":..., "tool_re":..., "error_re":...}, ...].
    A malformed file is ignored loudly rather than disabling the built-ins."""
    classes = list(BUILTIN_KILL_CLASSES)
    path = os.environ.get(KILL_PATTERNS_ENV)
    if path:
        try:
            with open(path) as f:
                extra = json.load(f)
            classes += [c for c in extra
                        if isinstance(c, dict) and c.get("class")
                        and c.get("tool_re") and c.get("error_re")]
        except (OSError, ValueError) as e:
            print(f"tool_ceiling: ignoring {KILL_PATTERNS_ENV}={path}: {e}")
    return classes


def _result_text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


class CeilingTracker:
    """Pairs tool_use frames with their tool_result and fires on a ceiling-kill error.
    observe(frame) returns True when the session should be stopped for a re-issue;
    .hit then holds {"class", "tool", "command", "error"}. budget<=0 disables
    detection entirely (the re-issue cap has been spent on this attempt)."""

    def __init__(self, budget=None):
        self.budget = REISSUE_MAX if budget is None else budget
        self.classes = [(c["class"], re.compile(c["tool_re"]), re.compile(c["error_re"]))
                        for c in kill_classes()]
        self.pending = {}            # tool_use_id -> (tool, command)
        self.hit = None

    def observe(self, frame):
        if self.hit:
            return True
        if self.budget <= 0:
            return False
        _msg = frame.get("message")
        content = (_msg.get("content") if isinstance(_msg, dict) else None) or []
        if frame.get("type") == "assistant":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    cmd = (b.get("input") or {}).get("command")
                    if cmd:
                        self.pending[b.get("id")] = (b.get("name") or "", cmd)
        elif frame.get("type") == "user":
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"
                        and b.get("is_error")):
                    continue
                tool, cmd = self.pending.pop(b.get("tool_use_id"), ("", None))
                if not cmd:
                    continue
                err = _result_text(b)
                for cls, tool_re, err_re in self.classes:
                    if tool_re.search(tool) and err_re.search(err):
                        self.hit = {"class": cls, "tool": tool,
                                    "command": cmd, "error": err[:500]}
                        return True
        return False


class _CallbackSink:
    """One-shot localhost HTTP sink for watchdog --callback: any POST sets the event so
    reissue() wakes the moment the verdict lands instead of at the next poll. Best
    effort — result.json polling is the correctness path, this is only latency."""

    def __init__(self):
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.end_headers()
                sink.event.set()

            def log_message(self, *a):
                pass

        self.event = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass


def _sanitize_label(label):
    return re.sub(r"[^A-Za-z0-9._-]", "_", label)   # mirrors watchdog's own rule


def result_path(label):
    return f"/tmp/watchdog-{_sanitize_label(label)}.result.json"


def _read_result(path, attempts=3, gap_s=1.0):
    """jq writes result.json via plain redirect, so a reader can catch a partial file;
    retry the parse briefly before giving up."""
    for i in range(attempts):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            if i + 1 < attempts:
                time.sleep(gap_s)
    return None


def _advisory(command, reason, detail=""):
    return (
        "[fworker tool-ceiling] Your Bash call was killed by the harness tool ceiling, "
        "not by the command itself:\n"
        f"  command: {command}\n"
        f"  {reason}{(' — ' + detail) if detail else ''}\n"
        "Verify the command's effects yourself before re-running anything, and keep "
        "individual Bash calls under the ceiling (run long jobs detached and poll). "
        "Then continue the ticket from where you left off.")


def _verdict(command, res):
    status = res.get("status") or "?"
    rc = res.get("rc")
    note = res.get("note") or ""
    return (
        "[fworker tool-ceiling re-issue] Your Bash call was killed by the harness tool "
        "ceiling, NOT by the command itself. fworker re-ran it to completion under a "
        "detached watchdog. Trust this verdict instead of the earlier timeout error:\n"
        f"  command: {command}\n"
        f"  verdict: {status} (exit {rc}) — {note}\n"
        f"  full output: {res.get('out')}   last lines: {res.get('tail')}\n"
        "Do NOT re-run the command. Read the output files if you need details, then "
        "continue the ticket from where you left off.")


def reissue(hit, *, label, cwd, env, run_dir_path, timeout_min=None, wait_slack_s=None,
            poll_s=5.0, heartbeat=None, heartbeat_every_s=600, log=print,
            watchdog_bin=None, mask=None):
    """Re-run hit["command"] under watchdog and block (bounded) for its verdict.

    Returns the text to resume the killed session with — a re-run verdict on the happy
    path, an advisory when the command is on the never-reissue list, could not be
    started, or outlived the wait budget (then it is STILL RUNNING detached and the
    advisory names the files to poll). Returns None only when there is nothing useful
    to tell the session (no command in the hit). Heartbeats the wq lease while waiting
    so a long re-run cannot lose the claim."""
    command = (hit or {}).get("command")
    if not command:
        return None
    watchdog_bin = watchdog_bin or WATCHDOG_BIN
    timeout_min = REISSUE_TIMEOUT_MIN if timeout_min is None else timeout_min
    wait_slack_s = WAIT_SLACK_S if wait_slack_s is None else wait_slack_s
    mask = mask or (lambda t: t)
    record = {"label": label, "kill_class": hit.get("class"), "command": command,
              "error": hit.get("error"), "started_at": time.time()}

    def finish(status, text, result=None):
        record.update({"status": status, "result": result,
                       "finished_at": time.time()})
        try:
            with open(os.path.join(run_dir_path, f"ceiling-reissue-{_sanitize_label(label)}.json"),
                      "w") as f:
                json.dump(record, f, indent=1)
        except OSError as e:
            log(f"tool_ceiling: reissue record write failed: {e}")
        return mask(text)

    if NEVER_REISSUE_RE.search(command):
        log(f"tool_ceiling: {label}: command is on the never-reissue list — advisory only")
        return finish("not_reissued",
                      _advisory(command, "The command was NOT re-run automatically "
                                "(it lands/publishes state; a blind second try could "
                                "double-apply a half-applied first try)."))

    sink = None
    try:
        try:
            sink = _CallbackSink()
        except OSError as e:
            log(f"tool_ceiling: callback sink unavailable ({e}) — polling only")
        argv = [watchdog_bin, "--no-send", "--label", label,
                "--timeout", str(timeout_min), "--poll", str(max(1, int(poll_s))),
                "--cwd", cwd]
        if sink:
            argv += ["--callback", sink.url]
        argv += ["--", command]
        res_path = result_path(label)
        try:
            spawn = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"tool_ceiling: watchdog spawn failed: {e}")
            return finish("spawn_failed",
                          _advisory(command, "fworker tried to re-run it under a "
                                    "watchdog but the watchdog could not be started",
                                    str(e)[:200]))
        if spawn.returncode != 0:
            tail = (spawn.stderr or spawn.stdout or "").strip()[-200:]
            log(f"tool_ceiling: watchdog refused (exit {spawn.returncode}): {tail}")
            return finish("spawn_failed",
                          _advisory(command, "fworker tried to re-run it under a "
                                    "watchdog but the watchdog refused to start", tail))
        log(f"tool_ceiling: re-issued under watchdog label={label} "
            f"timeout={timeout_min}m — waiting for {res_path}")

        deadline = time.time() + timeout_min * 60 + wait_slack_s
        last_beat = time.time()
        while time.time() < deadline:
            if sink:
                sink.event.wait(timeout=min(poll_s, 5))
            else:
                time.sleep(min(poll_s, 5))
            if os.path.exists(res_path):
                res = _read_result(res_path)
                if res is not None:
                    return finish("reissued", _verdict(command, res), result=res)
            if heartbeat and time.time() - last_beat >= heartbeat_every_s:
                heartbeat()
                last_beat = time.time()
        log(f"tool_ceiling: {label}: no result.json within "
            f"{timeout_min * 60 + wait_slack_s:.0f}s — resuming with a still-running advisory")
        return finish("wait_timeout",
                      _advisory(command,
                                "fworker re-ran it under a detached watchdog, but the "
                                "verdict did not arrive in time — the re-run may STILL "
                                "be running",
                                f"poll {res_path} and /tmp/watchdog-{_sanitize_label(label)}.out "
                                f"yourself (do not start another copy)"))
    finally:
        if sink:
            sink.close()
