"""wq#2185: measure the Claude CLI's fixed per-request overhead for the local profile.

Runs the exact command line fworker builds (fworker.build_claude_cmd) against a tiny
local HTTP stub that records every request body and answers each with a canned
end_turn, then counts tokens with charging.estimate_tokens (the chars/3.5 rule the size
gate uses). The number lives in charging.LOCAL_CLI_OVERHEAD_TOKENS next to
CALIBRATED_CLI_VERSION; a unit test fails when the installed CLI differs from that pin,
and this module is how you re-measure:

    python3 harness/cli_calibration.py            # local profile vs the bare command
    python3 harness/cli_calibration.py --json
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import charging  # noqa: E402

PROBE_PROMPT = "ok"
PROBE_MODEL = "hathor-llama8b"
PROBE_TIMEOUT_S = 40


def installed_cli_version(claude_bin="claude"):
    """`2.1.276 (Claude Code)` -> `2.1.276`; None when the CLI cannot be run."""
    try:
        out = subprocess.run([claude_bin, "--version"], capture_output=True, text=True,
                             timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[0] if out else None


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


_STOP_STREAM = b"".join([
    _sse("message_start", {"type": "message_start", "message": {
        "id": "msg_stub", "type": "message", "role": "assistant", "model": PROBE_MODEL,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}}}),
    _sse("content_block_start", {"type": "content_block_start", "index": 0,
                                  "content_block": {"type": "text", "text": ""}}),
    _sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                  "delta": {"type": "text_delta", "text": "ok"}}),
    _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
    _sse("message_delta", {"type": "message_delta", "usage": {"output_tokens": 1},
                            "delta": {"stop_reason": "end_turn", "stop_sequence": None}}),
    _sse("message_stop", {"type": "message_stop"}),
])
_STOP_JSON = json.dumps({
    "id": "msg_stub", "type": "message", "role": "assistant", "model": PROBE_MODEL,
    "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn",
    "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()


def _tool_use_stream(command):
    """A canned Bash tool call: the CLI's permission check for a command is what fires
    the small/fast-model side request, and a plain end_turn never reaches it."""
    return b"".join([
        _sse("message_start", {"type": "message_start", "message": {
            "id": "msg_stub", "type": "message", "role": "assistant", "model": PROBE_MODEL,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1}}}),
        _sse("content_block_start", {"type": "content_block_start", "index": 0,
                                      "content_block": {"type": "tool_use", "id": "toolu_stub",
                                                        "name": "Bash", "input": {}}}),
        _sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {
            "type": "input_json_delta", "partial_json": json.dumps({"command": command})}}),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse("message_delta", {"type": "message_delta", "usage": {"output_tokens": 1},
                                "delta": {"stop_reason": "tool_use", "stop_sequence": None}}),
        _sse("message_stop", {"type": "message_stop"}),
    ])


class _Stub(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, tool_command=None):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests = []
        self.tool_command = tool_command

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, body, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(json.dumps({"data": [{"id": PROBE_MODEL, "type": "model"}]}).encode())

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        if self.path.endswith("/count_tokens"):
            return self._reply(json.dumps({"input_tokens": 1}).encode())
        self.server.requests.append({"path": self.path, "body": body})
        answered = any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for m in body.get("messages") or ()
            for b in (m.get("content") if isinstance(m.get("content"), list) else ()))
        if self.server.tool_command and body.get("tools") and not answered:
            return self._reply(_tool_use_stream(self.server.tool_command), "text/event-stream")
        if body.get("stream"):
            return self._reply(_STOP_STREAM, "text/event-stream")
        self._reply(_STOP_JSON)


def _text_of(content):
    if isinstance(content, str):
        return content
    parts = []
    for block in content or ():
        if isinstance(block, dict):
            parts.append(block.get("text") or json.dumps(block, sort_keys=True))
    return "\n".join(parts)


def request_text(body):
    """Everything the backend has to prefill: system, tool definitions, messages."""
    parts = [_text_of(body.get("system")), json.dumps(body.get("tools") or [], sort_keys=True)]
    parts += [_text_of(m.get("content")) for m in body.get("messages") or ()]
    return "\n".join(parts)


def request_summary(req):
    body = req["body"]
    tools = [t.get("name") for t in body.get("tools") or ()]
    return {"model": body.get("model"), "tools": tools,
            "tokens_est": charging.estimate_tokens(request_text(body)),
            "parts_tokens_est": {
                "system": charging.estimate_tokens(_text_of(body.get("system"))),
                "tools": charging.estimate_tokens(json.dumps(body.get("tools") or [],
                                                              sort_keys=True)),
                "messages": charging.estimate_tokens("\n".join(
                    _text_of(m.get("content")) for m in body.get("messages") or ()))}}


def _scratch_repo(root):
    repo = os.path.join(root, "repo")
    os.makedirs(repo)
    env = dict(os.environ, GIT_AUTHOR_NAME="cal", GIT_AUTHOR_EMAIL="cal@example.invalid",
               GIT_COMMITTER_NAME="cal", GIT_COMMITTER_EMAIL="cal@example.invalid")
    for step in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "calibration"]):
        subprocess.run(["git", *step], cwd=repo, env=env, check=True, capture_output=True)
    return repo


def representative_prompt():
    """A rendered mechanical-edit prompt with a ~2.7k-char ticket: long enough that the
    CLI's session-title side call fires when nothing stops it."""
    import fworker
    tmpl = fworker.local_prompt_template(PROBE_MODEL, "mechanical-edit")
    return fworker.load_local_prompt(
        tmpl, 1891, {"title": "representative ticket", "detail": "Change the thing. " * 150},
        "/home/lumen/worktrees/wq1891-a1")


def measure(profile, claude_bin="claude", model=PROBE_MODEL, dump_dir=None,
            tool_command=None, prompt=PROBE_PROMPT):
    """Run one probe attempt. `profile` True is the local profile (flags + env as the
    dispatcher's local-rung workers get them); False is the bare cloud command line.
    Returns {"requests": [summary...], "first_request_tokens": n, "overhead_tokens": n,
    "exit_code": rc}. overhead_tokens is the first request minus the probe prompt."""
    import fworker  # heavy import; only the calibration path needs it
    with tempfile.TemporaryDirectory(prefix="cli-cal-") as root:
        repo = _scratch_repo(root)
        home = os.path.join(root, "home")
        os.makedirs(home)
        stub = _Stub(tool_command)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        try:
            env = {k: v for k, v in os.environ.items()
                   if k in ("PATH", "LANG", "LC_ALL", "TERM")}
            env.update(HOME=home, ANTHROPIC_BASE_URL=stub.url, ANTHROPIC_AUTH_TOKEN="probe",
                       CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY="1")
            if profile:
                env.update(fworker.local_profile_env())
            cmd = fworker.build_claude_cmd(
                prompt, model, None, fworker.SETTINGS_FILE, add_dirs=[],
                local_profile=profile, claude_bin=claude_bin)
            try:
                exit_code = subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                                           text=True, timeout=PROBE_TIMEOUT_S).returncode
            except subprocess.TimeoutExpired:
                exit_code = None  # the requests recorded so far are still the measurement
        finally:
            stub.shutdown()
        if dump_dir:
            for i, r in enumerate(stub.requests):
                with open(os.path.join(dump_dir, f"{'local' if profile else 'bare'}-{i}.json"),
                          "w") as f:
                    json.dump(r["body"], f, indent=1)
        reqs = [request_summary(r) for r in stub.requests]
    first = reqs[0]["tokens_est"] if reqs else None
    own = charging.estimate_tokens(prompt)
    return {"requests": reqs, "first_request_tokens": first,
            "overhead_tokens": None if first is None else max(0, first - own),
            "exit_code": exit_code}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dump", metavar="DIR", help="write each recorded request body here")
    args = ap.parse_args(argv)
    prompt = representative_prompt()
    out = {"cli_version": installed_cli_version(args.claude_bin),
           "recorded_cli_version": charging.CALIBRATED_CLI_VERSION,
           "recorded_overhead_tokens": charging.LOCAL_CLI_OVERHEAD_TOKENS,
           "measured_overhead_tokens": measure(True, args.claude_bin)["overhead_tokens"],
           "representative_prompt_tokens": charging.estimate_tokens(prompt),
           "bare": measure(False, args.claude_bin, dump_dir=args.dump, prompt=prompt),
           "local_profile": measure(True, args.claude_bin, dump_dir=args.dump, prompt=prompt)}
    print(json.dumps(out, indent=None if args.json else 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
