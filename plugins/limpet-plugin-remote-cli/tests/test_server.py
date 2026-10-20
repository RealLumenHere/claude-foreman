"""Tests for the shared-terminal MCP server (wq#4419, replacing the wq#4396
remote-cli wrapper's suite).

Three layers, in the order they catch things:

1. JSON-RPC shape tests (no tmux) — mirror limpet-plugin-flashrom's suite.
2. CLI argv-mapping tests against a recorded `_run`: every tool must put only
   real shared-terminal subcommands and flags on the command line. This is the
   same lesson as the retired suite's wire-protocol layer (ISSUE-LOG row 1473:
   a wrapper tested only against its own invented fake shipped 8/8 green with
   0/4 tools working) — the contract under test is the CLI's, pinned by
   tests/fixtures/shared_terminal_cli.json, not this wrapper's assumptions.
3. Live integration: a real tmux session created through the real
   shared-terminal CLI on an ISOLATED tmux server socket (never the fleet's
   `shared` socket), exercising start/send/wait/snapshot/scrollback/status/list
   end to end. Skips if tmux or the CLI is absent.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
import server  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "shared_terminal_cli.json")
with open(FIXTURE) as fh:
    CLI = json.load(fh)

SC_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "serial_console_cli.json")
with open(SC_FIXTURE) as fh:
    SC_CLI = json.load(fh)

TMUX = shutil.which("tmux")
ST_BIN = server.SHARED_TERMINAL_BIN if os.path.exists(server.SHARED_TERMINAL_BIN) else ""
SC_BIN = server.SERIAL_CONSOLE_BIN if os.path.exists(server.SERIAL_CONSOLE_BIN) else ""
needs_live = pytest.mark.skipif(
    not (TMUX and ST_BIN),
    reason="tmux or the shared-terminal CLI not found (set SHARED_TERMINAL_BIN)")

READ_ONLY_TOOLS = ["list", "scrollback", "snapshot", "status", "wait", "serial_console_expect"]
ENERGIZING_TOOLS = ["send", "start", "serial_console_open", "serial_console_catch"]


# --------------------------------------------------------------------------
# 1. JSON-RPC shape
# --------------------------------------------------------------------------

def test_initialize():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["serverInfo"]["name"] == "limpet-shared-terminal-mcp"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert sorted(names) == sorted(READ_ONLY_TOOLS + ENERGIZING_TOOLS)


def test_unknown_tool():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "exec", "arguments": {}}})
    assert "error" in resp, "the retired remote-cli exec tool must be gone"


def test_initialized_notification():
    resp = server.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    assert resp is None


def test_unknown_method():
    resp = server.handle_request({"jsonrpc": "2.0", "id": 5, "method": "foo/bar", "params": {}})
    assert "error" in resp


def test_manifest_energize_classification_matches_the_tools():
    """The ticket-pinned split: snapshot/scrollback/list/status/wait read-only,
    send/start energize-gated — checked against the actual manifest so the two
    files cannot drift apart."""
    manifest = os.path.join(os.path.dirname(__file__), "..", "limpet-plugin.toml")
    with open(manifest) as fh:
        text = fh.read()
    section = text.split("[mcp.tools_energize]", 1)[1].split("[agent_hints]", 1)[0]
    flags = {}
    for line in section.splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            key, val = (part.strip() for part in line.split("=", 1))
            flags[key] = val == "true"
    assert flags == {**{t: False for t in READ_ONLY_TOOLS},
                     **{t: True for t in ENERGIZING_TOOLS}}
    assert set(flags) == set(server.TOOLS), "manifest lists a tool the server lacks (or vice versa)"


# --------------------------------------------------------------------------
# 2. CLI argv mapping
# --------------------------------------------------------------------------

def _record_runs(monkeypatch, result=None):
    calls = []

    def fake_run(args, timeout=server.REQUEST_TIMEOUT):
        calls.append(list(args))
        return dict(result or {"ok": True, "exit_code": 0, "output": ""})

    monkeypatch.setattr(server, "_run", fake_run)
    return calls


def _assert_real_cli_shape(argv):
    sub = argv[0]
    assert sub in CLI["subcommands"], f"not a shared-terminal subcommand: {argv}"
    for flag in (a for a in argv[1:] if a.startswith("--")):
        assert flag in CLI["subcommands"][sub]["flags"], \
            f"{sub} has no flag {flag!r} in the real CLI"


def test_every_tool_uses_only_real_cli_subcommands(monkeypatch):
    calls = _record_runs(monkeypatch)
    server.tool_start("s", command="htop")
    server.tool_send("s", text="ls")
    server.tool_send("s", keys=["C-c"])
    server.tool_snapshot("s", ansi=True)
    server.tool_scrollback("s", lines=50)
    server.tool_scrollback("s", all=True)
    server.tool_list()
    server.tool_status("s")
    server.tool_wait("s", r"\$", timeout=5)
    assert calls, "no CLI invocations recorded"
    for argv in calls:
        _assert_real_cli_shape(argv)


def test_send_text_uses_no_enter_plus_explicit_enter_key(monkeypatch):
    """Literal text must go through --no-enter + --key Enter, so text beginning
    with `--` can never be parsed as a CLI flag."""
    calls = _record_runs(monkeypatch)
    server.tool_send("bench", text="--key C-c")
    assert calls == [["send", "bench", "--no-enter", "--key C-c"],
                     ["send", "bench", "--key", "Enter"]]


def test_send_text_without_enter_types_only(monkeypatch):
    calls = _record_runs(monkeypatch)
    server.tool_send("bench", text="partial", enter=False)
    assert calls == [["send", "bench", "--no-enter", "partial"]]


def test_send_keys_maps_to_key_names(monkeypatch):
    calls = _record_runs(monkeypatch)
    server.tool_send("bench", keys=["C-c", "Up", "Enter"])
    assert calls == [["send", "bench", "--key", "C-c", "Up", "Enter"]]


def test_send_without_text_or_keys_is_an_error(monkeypatch):
    calls = _record_runs(monkeypatch)
    result = server.tool_send("bench")
    assert result["ok"] is False and not calls


def test_send_failed_typing_does_not_press_enter(monkeypatch):
    """If the text never reached the session (no such session, dead server),
    following up with Enter would be a blind keystroke into whatever exists."""
    calls = _record_runs(monkeypatch, result={"ok": False, "exit_code": 1, "error": "no session"})
    result = server.tool_send("bench", text="ls")
    assert result["ok"] is False
    assert calls == [["send", "bench", "--no-enter", "ls"]]


def test_scrollback_lines_and_all(monkeypatch):
    calls = _record_runs(monkeypatch)
    server.tool_scrollback("bench")
    server.tool_scrollback("bench", lines=17)
    server.tool_scrollback("bench", all=True)
    assert calls == [["scrollback", "bench", "200"],
                     ["scrollback", "bench", "17"],
                     ["scrollback", "bench", "all"]]


def test_wait_timeout_is_whole_seconds_and_exit_2_means_timed_out(monkeypatch):
    calls = _record_runs(monkeypatch, result={"ok": False, "exit_code": 2, "output": "",
                                              "error": "timeout waiting for /x/"})
    result = server.tool_wait("bench", "READY", timeout=7.9)
    assert calls == [["wait", "bench", "READY", "7"]]
    assert result["timed_out"] is True


def test_status_parses_the_cli_json(monkeypatch):
    payload = {"session": "bench", "attached_clients": 1, "cols": 120, "rows": 40,
               "shell_pid": 4242, "running": "bash", "cwd": "/home/lumen",
               "created": "x", "log": "/tmp/bench.log"}
    _record_runs(monkeypatch, result={"ok": True, "exit_code": 0,
                                      "output": json.dumps(payload)})
    result = server.tool_status("bench")
    assert result["ok"] is True
    assert result["status"] == payload
    assert "output" not in result


def test_run_reports_a_missing_binary_as_an_error():
    monkey_bin = os.path.join(tempfile.gettempdir(), "wq4419-no-such-shared-terminal")
    real = server.SHARED_TERMINAL_BIN
    server.SHARED_TERMINAL_BIN = monkey_bin
    try:
        result = server._run(["list"])
    finally:
        server.SHARED_TERMINAL_BIN = real
    assert result["ok"] is False and "not found" in result["error"]


# --------------------------------------------------------------------------
# 2b. serial-console CLI argv mapping (wq#4433)
# --------------------------------------------------------------------------

def _record_serial_runs(monkeypatch, result=None):
    calls = []

    def fake_run(args, timeout=server.REQUEST_TIMEOUT):
        calls.append(list(args))
        return dict(result or {"ok": True, "exit_code": 0, "output": ""})

    monkeypatch.setattr(server, "_run_serial_console", fake_run)
    return calls


def _assert_real_serial_cli_shape(argv):
    sub = argv[0]
    assert sub in SC_CLI["subcommands"], f"not a serial-console subcommand: {argv}"
    for flag in (a for a in argv[1:] if a.startswith("--")):
        assert flag in SC_CLI["subcommands"][sub]["flags"], \
            f"{sub} has no flag {flag!r} in the real CLI"


def test_serial_console_every_tool_uses_only_real_cli_subcommands(monkeypatch):
    calls = _record_serial_runs(monkeypatch)
    server.tool_serial_console_open(dev="/dev/ttyUSB0", session="c130", baud=115200)
    server.tool_serial_console_catch("c130", "login:", hz=10, secs=5)
    server.tool_serial_console_catch("c130", "login:", keys=["Space"])
    server.tool_serial_console_expect("c130", "login:", timeout=5)
    assert calls, "no CLI invocations recorded"
    for argv in calls:
        _assert_real_serial_cli_shape(argv)


def test_serial_console_open_bare_autodetects(monkeypatch):
    calls = _record_serial_runs(monkeypatch)
    server.tool_serial_console_open()
    assert calls == [["open"]]


def test_serial_console_open_session_without_dev_is_rejected(monkeypatch):
    """The CLI takes dev/session/baud positionally; a session name with no dev
    would silently land in the dev slot instead, so this must error, not call
    the CLI with the wrong positional meaning."""
    calls = _record_serial_runs(monkeypatch)
    result = server.tool_serial_console_open(session="c130")
    assert result["ok"] is False and not calls


def test_serial_console_open_baud_without_session_is_rejected(monkeypatch):
    calls = _record_serial_runs(monkeypatch)
    result = server.tool_serial_console_open(dev="/dev/ttyUSB0", baud=9600)
    assert result["ok"] is False and not calls


def test_serial_console_open_empty_string_dev_is_rejected(monkeypatch):
    """dev='' is not dev=None, but it carries no real device either; session/baud
    must not be silently dropped for it the way truthiness-gated arg-building would."""
    calls = _record_serial_runs(monkeypatch)
    result = server.tool_serial_console_open(dev="", session="c130", baud=9600)
    assert result["ok"] is False and not calls


def test_serial_console_catch_default_mode_is_enter(monkeypatch):
    calls = _record_serial_runs(monkeypatch)
    server.tool_serial_console_catch("c130", "login:")
    assert calls == [["catch", "c130", "login:", "--enter", "--hz", "20", "--secs", "60"]]


def test_serial_console_catch_keys_mode(monkeypatch):
    calls = _record_serial_runs(monkeypatch)
    server.tool_serial_console_catch("c130", "login:", keys=["Space", "Enter"], hz=5, secs=20)
    assert calls == [["catch", "c130", "login:", "--keys", "Space", "Enter", "--hz", "5", "--secs", "20"]]


def test_serial_console_catch_exit_2_means_timed_out(monkeypatch):
    _record_serial_runs(monkeypatch, result={"ok": False, "exit_code": 2, "output": "",
                                             "error": "timeout, /login:/ not seen in 5s"})
    result = server.tool_serial_console_catch("c130", "login:", secs=5)
    assert result["timed_out"] is True


def test_serial_console_expect_timeout_is_whole_seconds(monkeypatch):
    calls = _record_serial_runs(monkeypatch)
    server.tool_serial_console_expect("c130", "login:", timeout=7.9)
    assert calls == [["expect", "c130", "login:", "7"]]


def test_serial_console_expect_exit_2_means_timed_out(monkeypatch):
    _record_serial_runs(monkeypatch, result={"ok": False, "exit_code": 2, "output": "",
                                             "error": "timeout waiting for /login:/"})
    result = server.tool_serial_console_expect("c130", "login:", timeout=5)
    assert result["timed_out"] is True


def test_serial_console_run_reports_a_missing_binary_as_an_error():
    monkey_bin = os.path.join(tempfile.gettempdir(), "wq4433-no-such-serial-console")
    real = server.SERIAL_CONSOLE_BIN
    server.SERIAL_CONSOLE_BIN = monkey_bin
    try:
        result = server._run_serial_console(["expect", "c130", "login:", "5"])
    finally:
        server.SERIAL_CONSOLE_BIN = real
    assert result["ok"] is False and "not found" in result["error"]


def test_fixture_matches_real_serial_console_cli_help():
    """Drift guard, mirroring test_fixture_matches_real_cli_help: if
    serial-console grows or renames subcommands, fail loudly instead of
    letting the wrapper speak a stale surface."""
    if not SC_BIN:
        pytest.skip("serial-console CLI not found (set SERIAL_CONSOLE_BIN)")
    with open(SC_BIN) as fh:
        source = fh.read()
    for sub in SC_CLI["subcommands"]:
        assert f"{sub})" in source, f"fixture pins subcommand {sub!r} the CLI lacks"
    for sub, spec in SC_CLI["subcommands"].items():
        for flag in spec["flags"]:
            assert flag in source, f"fixture pins flag {flag!r} the CLI lacks"


# --------------------------------------------------------------------------
# 3. Live integration: real tmux through the real shared-terminal CLI
# --------------------------------------------------------------------------

class LiveSharedTerminal:
    """A real shared-terminal session on an isolated tmux server socket, so
    the fleet's actual `shared` socket is never touched by tests."""

    def __init__(self):
        self.socket = f"wq4419test{uuid.uuid4().hex[:8]}"
        self.logs = tempfile.mkdtemp(prefix="wq4419-shared-terminal-logs-")
        self.session = "t1"
        os.environ["SHARED_TERMINAL_SOCKET"] = self.socket
        os.environ["SHARED_TERMINAL_LOGS"] = self.logs
        proc = subprocess.run(
            [ST_BIN, "start", self.session, "/bin/bash --norc --noprofile"],
            capture_output=True, text=True, timeout=15)
        assert proc.returncode == 0, (proc.stdout, proc.stderr)

    def close(self):
        subprocess.run([TMUX, "-L", self.socket, "kill-server"],
                       capture_output=True, timeout=10)
        os.environ.pop("SHARED_TERMINAL_SOCKET", None)
        os.environ.pop("SHARED_TERMINAL_LOGS", None)
        shutil.rmtree(self.logs, ignore_errors=True)


@pytest.fixture(scope="module")
def live():
    if not (TMUX and ST_BIN):
        pytest.skip("tmux or shared-terminal CLI missing")
    lt = LiveSharedTerminal()
    try:
        yield lt
    finally:
        lt.close()


@needs_live
def test_live_health_check_socket_exists(live):
    """The manifest's [health] check target: the tmux server socket for this
    (isolated) server name, at the path tmux actually creates."""
    sock = f"/tmp/tmux-{os.getuid()}/{live.socket}"
    assert os.path.exists(sock), sock


@needs_live
def test_live_list_and_status(live):
    listing = server.tool_list()
    assert listing["ok"] is True and live.session in listing["output"], listing
    status = server.tool_status(live.session)
    assert status["ok"] is True, status
    assert status["status"]["session"] == live.session
    assert status["status"]["cols"] > 0 and status["status"]["rows"] > 0


@needs_live
def test_live_send_wait_snapshot_scrollback_roundtrip(live):
    marker = "wq4419-live-roundtrip-ok"
    sent = server.tool_send(live.session, text=f"echo {marker}")
    assert sent["ok"] is True, sent
    waited = server.tool_wait(live.session, marker, timeout=15)
    assert waited["ok"] is True, waited
    snap = server.tool_snapshot(live.session)
    assert marker in snap["output"], snap
    back = server.tool_scrollback(live.session, lines=50)
    assert marker in back["output"], back


@needs_live
def test_live_send_keys_interrupts_a_running_command(live):
    assert server.tool_send(live.session, text="sleep 300")["ok"] is True
    time.sleep(0.5)
    assert server.tool_send(live.session, keys=["C-c"])["ok"] is True
    waited = server.tool_wait(live.session, r"\$", timeout=10)
    assert waited["ok"] is True, waited
    status = server.tool_status(live.session)
    assert status["status"]["running"] != "sleep", status


@needs_live
def test_live_wait_times_out_on_a_regex_that_never_appears(live):
    """The probe that can fail: a regex matching nothing on screen must come
    back timed_out (CLI exit 2), not ok and not a crash."""
    started = time.monotonic()
    result = server.tool_wait(live.session, "wq4419-never-printed-anywhere", timeout=2)
    elapsed = time.monotonic() - started
    assert result.get("timed_out") is True, result
    assert result["ok"] is False
    assert 1.5 <= elapsed < 10.0, f"timeout not honored: returned after {elapsed:.2f}s"


@needs_live
def test_live_tools_against_a_missing_session_report_an_error(live):
    for result in (server.tool_snapshot("wq4419-no-such-session"),
                   server.tool_status("wq4419-no-such-session"),
                   server.tool_send("wq4419-no-such-session", text="x")):
        assert result["ok"] is False, result
        assert "no session" in result.get("error", ""), result


@needs_live
def test_live_transcript_log_is_written(live):
    marker = "wq4419-transcript-probe"
    assert server.tool_send(live.session, text=f"echo {marker}")["ok"] is True
    log = os.path.join(live.logs, f"{live.session}.log")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if os.path.exists(log):
            with open(log, errors="replace") as fh:
                if marker in fh.read():
                    return
        time.sleep(0.2)
    pytest.fail(f"marker never reached the transcript log {log}")


@needs_live
def test_fixture_matches_real_cli_help():
    """Drift guard: if the shared-terminal CLI grows or renames subcommands,
    this fails loudly instead of letting the wrapper speak a stale surface."""
    with open(ST_BIN) as fh:
        source = fh.read()
    for sub in CLI["subcommands"]:
        assert f"{sub})" in source, f"fixture pins subcommand {sub!r} the CLI lacks"
    for sub, spec in CLI["subcommands"].items():
        for flag in spec["flags"]:
            assert flag in source, f"fixture pins flag {flag!r} the CLI lacks"
