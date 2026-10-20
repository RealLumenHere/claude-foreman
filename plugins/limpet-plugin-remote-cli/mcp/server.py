#!/usr/bin/env python3
"""limpet-shared-terminal-mcp — MCP server wrapping the shared-terminal CLI,
the tmux-backed shared session tool (lumen-bin PR #262, 2026-10-19;
design/diagnosis: scratch/research/shared-terminal-solution.md).

wq#4419: this plugin previously wrapped remote-cli, wq#4395's custom shared-PTY
daemon (Unix-socket JSON protocol, client-side synthesized `exec`). remote-cli
is retired: its attach showed a blank screen until a key was pressed (no screen
replay), it was pinned to 24x80 with no resize/detach, and its VT100 subset
could not render picocom/mtkclient/adb output faithfully. shared-terminal is
tmux on a dedicated server socket (`tmux -L shared`), which does all of that
natively — including the run-to-completion plumbing the old wrapper had to
synthesize with marker printf lines (`wait` on a prompt regex replaces it).

Every tool shells out to the shared-terminal CLI itself rather than driving
tmux directly, so the CLI stays the single source of truth for socket naming,
transcript logging, and capture semantics, and a human using the same commands
sees identical behaviour. The CLI surface is unchanged by this plugin.

Energize classification (wq#4419 ticket-specified):
  snapshot   = read_only  — capture-pane of the current screen, no write
  scrollback = read_only  — capture-pane of history, no write
  list       = read_only  — session inventory
  status     = read_only  — session metadata (size, pid, cwd, log path)
  wait       = read_only  — polls capture-pane for a regex; never types
  send       = energizing — keystrokes into a terminal a human may be driving
               (passwords, control sequences, a stray Ctrl-C) are exactly what
               the energize-confirm gate exists for.
  start      = energizing — creates a session and may launch an arbitrary
               command in it; strictly more consequential than raw keys.

wq#4433 adds three tools wrapping `serial-console` (lumen-bin, one-port-owner
picocom inside a shared-terminal session; design: the 2026-10-19 AP-303/C-130
failure where a second pexpect-driven picocom fought the shared session for
the same /dev/ttyUSB0). serial-console's own snapshot/send/scrollback/status/
list/kill are a straight passthrough to shared-terminal, so the existing
tools above already cover them for a serial session — only open/catch/expect
are new surface:
  serial_console_open   = energizing — spawns `sudo picocom` as root, the
                           device's sole port owner.
  serial_console_catch  = energizing — spams a key (default Enter) into the
                           session until the screen matches a regex or a
                           timeout elapses; drives keys during a boot window
                           (Hit-any-key / Press-f prompts) exactly like `send`
                           does for an ordinary shell.
  serial_console_expect = read_only — polls the screen for a regex, same as
                           `wait`, but through serial-console's own subcommand
                           (no key spam).

wq#4443 adds three combined-verb tools matching re-term (lumen-bin, horus-local
until this ticket, sha 5440810c; usage: scratch/research/re-term-usage.md),
the agent-side fast driver for the bench serial console that holds a
persistent SSH ControlMaster to the RE box so a warm call is 0.04s against a
cold 0.8s. re-term's verb set (drive/open/snap/tail/do/type/at/key/catch/
expect/status/list/kill/close) is mostly already covered here — send/snapshot/
scrollback/status/list/wait/serial_console_* above are drive/type+key/snap/
tail/status/list/expect+catch under different names. Only the three verbs
that COMBINE a read and a write in one round trip are new surface:
  term_do    = energizing — send then snapshot in one call, so the agent sees
               the result of its own keystrokes without a second tool round
               trip (re-term's `do`).
  term_at    = energizing — wait for a regex, THEN send: a guarded send that
               never types blind into the wrong prompt (a hung boot, a stale
               shell, output still scrolling) (re-term's `at`).
  term_catch = energizing — same spam-until-match loop as
               serial_console_catch, generalized past serial sessions: the
               serial-console CLI's `catch` subcommand only requires
               `shared-terminal status` on the name, never a picocom-owned
               port, so this reuses it unchanged for a plain shell session.
This gives agents the fast do/at/catch loop through MCP without hand-SSH to
the RE box; the persistent-connection speedup itself lives in re-term's own
ControlMaster, not in this stdio-per-call wrapper.

Stdlib-only, same rationale as the rest of the first-party Limpet MCP servers
(limpet-plugin-flashrom/mcp/server.py): unit-testable without the `mcp` SDK.
Tool handlers never pass agent text where the CLI parses flags: literal text
goes through `send --no-enter` (whose parser takes everything after the flag
as data) followed by an explicit `--key Enter`, so text beginning with `--`
cannot be misread as an option.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "limpet-shared-terminal-mcp"
SERVER_VERSION = "0.3.0"

DEFAULT_BIN = "/home/lumen/bin/shared-terminal"
SHARED_TERMINAL_BIN = (
    os.environ.get("SHARED_TERMINAL_BIN")
    or (DEFAULT_BIN if os.path.exists(DEFAULT_BIN) else None)
    or shutil.which("shared-terminal")
    or DEFAULT_BIN
)

DEFAULT_SERIAL_CONSOLE_BIN = "/home/lumen/bin/serial-console"
SERIAL_CONSOLE_BIN = (
    os.environ.get("SERIAL_CONSOLE_BIN")
    or (DEFAULT_SERIAL_CONSOLE_BIN if os.path.exists(DEFAULT_SERIAL_CONSOLE_BIN) else None)
    or shutil.which("serial-console")
    or DEFAULT_SERIAL_CONSOLE_BIN
)

# One CLI invocation is a tmux client round-trip — fast. `wait` is the only
# long-running subcommand and gets its own deadline (the caller's timeout plus
# margin for the CLI's final poll sleep).
REQUEST_TIMEOUT = 10.0
WAIT_MARGIN = 5.0


def _run(args: List[str], timeout: float = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """Run one shared-terminal subcommand and shape the result for a tool
    reply: stdout is the payload, stderr is the CLI's own diagnostic line
    (`shared-terminal: no session 'x'`), the exit code separates ok from not.
    The subprocess inherits the environment, so SHARED_TERMINAL_SOCKET /
    SHARED_TERMINAL_LOGS overrides reach the CLI untouched."""
    try:
        proc = subprocess.run(
            [SHARED_TERMINAL_BIN, *args],
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False,
                "error": f"shared-terminal CLI not found at {SHARED_TERMINAL_BIN} "
                         "(set SHARED_TERMINAL_BIN)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "timed_out": True,
                "error": f"shared-terminal {args[0]} did not return within {timeout}s"}
    result: Dict[str, Any] = {"ok": proc.returncode == 0,
                              "exit_code": proc.returncode,
                              "output": proc.stdout.rstrip("\n")}
    if proc.returncode != 0:
        result["error"] = proc.stderr.strip() or f"shared-terminal {args[0]} failed"
    elif proc.stderr.strip():
        result["info"] = proc.stderr.strip()
    return result


def _run_serial_console(args: List[str], timeout: float = REQUEST_TIMEOUT) -> Dict[str, Any]:
    """Run one serial-console subcommand (open/catch/expect) — same result shape
    as `_run`, against a different CLI. serial-console's snapshot/send/scrollback/
    status/list/kill are a passthrough to shared-terminal already covered by the
    tools above, so this only needs to drive the three subcommands it owns."""
    try:
        proc = subprocess.run(
            [SERIAL_CONSOLE_BIN, *args],
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False,
                "error": f"serial-console CLI not found at {SERIAL_CONSOLE_BIN} "
                         "(set SERIAL_CONSOLE_BIN)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "timed_out": True,
                "error": f"serial-console {args[0]} did not return within {timeout}s"}
    result: Dict[str, Any] = {"ok": proc.returncode == 0,
                              "exit_code": proc.returncode,
                              "output": proc.stdout.rstrip("\n")}
    if proc.returncode != 0:
        result["error"] = proc.stderr.strip() or f"serial-console {args[0]} failed"
    elif proc.stderr.strip():
        result["info"] = proc.stderr.strip()
    return result


def tool_serial_console_open(dev: Optional[str] = None, session: Optional[str] = None,
                              baud: Optional[int] = None) -> Dict[str, Any]:
    """Open a picocom session on a UART device inside shared-terminal — the ONLY
    process allowed to own the port (serial-console refuses a busy port itself).
    ENERGIZING: spawns `sudo picocom` as root.

    The CLI takes dev/session/baud positionally (`open [dev] [name] [baud]`), so
    naming a session or baud without a device would silently shift onto the
    wrong positional slot; reject that combination instead of guessing."""
    if not dev and (session is not None or baud is not None):
        return {"ok": False, "error": "serial_console_open: session/baud need dev given too "
                                       "(CLI positional order: open [dev] [name] [baud])"}
    if not session and baud is not None:
        return {"ok": False, "error": "serial_console_open: baud needs session given too "
                                       "(CLI positional order: open [dev] [name] [baud])"}
    args = ["open"]
    if dev:
        args.append(dev)
        if session:
            args.append(session)
            if baud:
                args.append(str(int(baud)))
    return _run_serial_console(args)


def tool_serial_console_catch(session: str, regex: str, keys: Optional[List[str]] = None,
                               hz: int = 20, secs: int = 60) -> Dict[str, Any]:
    """Spam a key (Enter by default, or named tmux keys) into the session until
    the screen matches `regex` or `secs` elapses — the boot-window catch loop
    (Hit-any-key / Press-f prompts). ENERGIZING: types into a terminal a human
    may be watching, same category as `send`. Exit 2 from the CLI is a timeout."""
    args = ["catch", session, regex]
    if keys:
        args += ["--keys", *keys]
    else:
        args.append("--enter")
    args += ["--hz", str(int(hz)), "--secs", str(int(secs))]
    result = _run_serial_console(args, timeout=secs + WAIT_MARGIN)
    if result.get("exit_code") == 2:
        result["timed_out"] = True
    return result


def tool_serial_console_expect(session: str, regex: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Wait for the screen to match `regex`, no key spam. Read-only — polls the
    screen via serial-console's own `expect` subcommand (which delegates to
    `shared-terminal wait`) and never types. Exit 2 from the CLI is a timeout."""
    seconds = max(1, int(timeout))
    result = _run_serial_console(["expect", session, regex, str(seconds)],
                                 timeout=seconds + WAIT_MARGIN)
    if result.get("exit_code") == 2:
        result["timed_out"] = True
    return result


def tool_start(session: str, command: Optional[str] = None) -> Dict[str, Any]:
    """Create a detached session (optionally running `command`). ENERGIZING.
    Idempotent at the CLI layer: an existing session is reported, not clobbered."""
    args = ["start", session]
    if command:
        args.append(command)
    return _run(args)


def tool_send(session: str, text: Optional[str] = None, enter: bool = True,
              keys: Optional[List[str]] = None) -> Dict[str, Any]:
    """Type into the shared terminal. ENERGIZING — a human may be watching or
    driving the same session live.

    Two modes, mutually exclusive:
      keys — tmux key names (C-c, Enter, Up, Escape, F1 ...), sent verbatim.
      text — literal characters, then Enter unless enter=false. Sent via the
             CLI's --no-enter form plus an explicit `--key Enter`, so text
             beginning with `--` is never parsed as a flag."""
    if keys:
        return _run(["send", session, "--key", *keys])
    if text is None:
        return {"ok": False, "error": "send needs either text or keys"}
    if text:
        typed = _run(["send", session, "--no-enter", text])
        if not typed.get("ok") or not enter:
            return typed
    elif not enter:
        return {"ok": True, "exit_code": 0, "output": ""}
    return _run(["send", session, "--key", "Enter"])


def tool_snapshot(session: str, ansi: bool = False) -> Dict[str, Any]:
    """Current visible screen — what an attached human sees. Read-only."""
    args = ["snapshot", session]
    if ansi:
        args.append("--ansi")
    return _run(args)


def tool_scrollback(session: str, lines: int = 200, all: bool = False) -> Dict[str, Any]:
    """Last `lines` of history (joined wrapped lines), or everything with
    all=true. Read-only."""
    return _run(["scrollback", session, "all" if all else str(int(lines))])


def tool_list() -> Dict[str, Any]:
    """Active shared sessions with size, client count, and running command.
    Read-only."""
    return _run(["list"])


def tool_status(session: str) -> Dict[str, Any]:
    """Session metadata: size, attached clients, foreground command, cwd, pid,
    transcript log path. Read-only. The CLI emits one JSON object; it is parsed
    into the reply so callers get fields, not a string to re-parse."""
    result = _run(["status", session])
    if result.get("ok"):
        try:
            result["status"] = json.loads(result.pop("output"))
        except (json.JSONDecodeError, KeyError):
            pass
    return result


def tool_wait(session: str, regex: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Block until the visible screen matches `regex` (POSIX ERE, grep -E),
    up to `timeout` seconds. Read-only — it polls capture-pane and never
    types. Exit 2 from the CLI is a timeout, reported as timed_out rather
    than a hard error so callers can branch on it."""
    seconds = max(1, int(timeout))
    result = _run(["wait", session, regex, str(seconds)],
                  timeout=seconds + WAIT_MARGIN)
    if result.get("exit_code") == 2:
        result["timed_out"] = True
    return result


def tool_term_do(session: str, text: Optional[str] = None, enter: bool = True,
                  keys: Optional[List[str]] = None, ansi: bool = False) -> Dict[str, Any]:
    """Send then read back the screen in one round trip — re-term's `do` verb.
    ENERGIZING — requires energize-confirm (it types). A failed send is
    returned as-is, without a follow-up snapshot of whatever the screen
    happened to already show."""
    sent = tool_send(session, text=text, enter=enter, keys=keys)
    if not sent.get("ok"):
        return sent
    result = tool_snapshot(session, ansi=ansi)
    result["sent"] = sent
    return result


def tool_term_at(session: str, regex: str, text: Optional[str] = None, enter: bool = True,
                  keys: Optional[List[str]] = None, timeout: float = 30.0) -> Dict[str, Any]:
    """Guarded send — re-term's `at` verb: wait for the screen to match `regex`
    before typing, so a send never lands blind on the wrong prompt. ENERGIZING
    — requires energize-confirm. A guard that never matches is returned as-is,
    without sending anything."""
    waited = tool_wait(session, regex, timeout=timeout)
    if not waited.get("ok"):
        return waited
    sent = tool_send(session, text=text, enter=enter, keys=keys)
    sent["waited"] = waited
    return sent


def tool_term_catch(session: str, regex: str, keys: Optional[List[str]] = None,
                     hz: int = 20, secs: int = 60) -> Dict[str, Any]:
    """Spam a key into any shared-terminal session until the screen matches
    `regex`, or `secs` elapses — re-term's `catch` verb, generalized past
    serial-console sessions (same CLI call as serial_console_catch; the
    serial-console `catch` subcommand itself never requires a picocom-owned
    port). ENERGIZING — requires energize-confirm."""
    return tool_serial_console_catch(session, regex, keys=keys, hz=hz, secs=secs)


TOOLS = {
    "start": {
        "description": "Create a detached shared-terminal session (optionally running a command). ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "command": {"type": "string", "description": "Command to run in the session (default: a shell)"},
            },
            "required": ["session"],
        },
        "handler": tool_start,
    },
    "send": {
        "description": "Type text or key names into the shared terminal session. ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "text": {"type": "string", "description": "Literal text to type"},
                "enter": {"type": "boolean", "description": "Press Enter after text (default true)", "default": True},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "tmux key names instead of text: C-c, Enter, Up, Escape, F1 ..."},
            },
            "required": ["session"],
        },
        "handler": tool_send,
    },
    "snapshot": {
        "description": "Read the current visible screen of a shared session (what an attached human sees). Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "ansi": {"type": "boolean", "description": "Keep ANSI escape sequences", "default": False},
            },
            "required": ["session"],
        },
        "handler": tool_snapshot,
    },
    "scrollback": {
        "description": "Read a shared session's history (last N lines, or all). Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "lines": {"type": "integer", "description": "Trailing lines to return", "default": 200},
                "all": {"type": "boolean", "description": "Return the entire retained history", "default": False},
            },
            "required": ["session"],
        },
        "handler": tool_scrollback,
    },
    "list": {
        "description": "List active shared-terminal sessions. Read-only.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_list,
    },
    "status": {
        "description": "Session metadata: size, attached clients, running command, cwd, transcript log path. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
            },
            "required": ["session"],
        },
        "handler": tool_status,
    },
    "wait": {
        "description": "Block until the session's visible screen matches a regex (or timeout). Read-only — polls the screen, never types.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "regex": {"type": "string", "description": "POSIX extended regex to wait for"},
                "timeout": {"type": "number", "description": "Seconds to wait (default 30)", "default": 30.0},
            },
            "required": ["session", "regex"],
        },
        "handler": tool_wait,
    },
    "serial_console_open": {
        "description": "Open a picocom session on a UART device inside shared-terminal, the sole owner of the port. ENERGIZING — requires energize-confirm; spawns `sudo picocom` as root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dev": {"type": "string", "description": "Serial device, e.g. /dev/ttyUSB0 (autodetected if omitted and exactly one /dev/ttyUSB*|ttyACM* exists)"},
                "session": {"type": "string", "description": "Session name (default: device basename); requires dev"},
                "baud": {"type": "integer", "description": "Baud rate (default 115200); requires session"},
            },
        },
        "handler": tool_serial_console_open,
    },
    "serial_console_catch": {
        "description": "Spam a key into the session (Enter by default) until the screen matches a regex, or a timeout elapses — catches a boot-window prompt (Hit any key, Press f...). ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "regex": {"type": "string", "description": "POSIX extended regex to catch"},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "tmux key names to send each tick instead of Enter"},
                "hz": {"type": "integer", "description": "Key bursts per second (default 20)", "default": 20},
                "secs": {"type": "integer", "description": "Timeout in seconds (default 60)", "default": 60},
            },
            "required": ["session", "regex"],
        },
        "handler": tool_serial_console_catch,
    },
    "serial_console_expect": {
        "description": "Wait for a UART session's screen to match a regex, with no key spam. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "regex": {"type": "string", "description": "POSIX extended regex to wait for"},
                "timeout": {"type": "number", "description": "Seconds to wait (default 30)", "default": 30.0},
            },
            "required": ["session", "regex"],
        },
        "handler": tool_serial_console_expect,
    },
    "term_do": {
        "description": "Send text or keys, then read back the screen in one round trip (re-term's `do` verb). ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "text": {"type": "string", "description": "Literal text to type"},
                "enter": {"type": "boolean", "description": "Press Enter after text (default true)", "default": True},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "tmux key names instead of text: C-c, Enter, Up, Escape, F1 ..."},
                "ansi": {"type": "boolean", "description": "Keep ANSI escape sequences in the returned screen", "default": False},
            },
            "required": ["session"],
        },
        "handler": tool_term_do,
    },
    "term_at": {
        "description": "Wait for the screen to match a regex, then send text or keys — a guarded send that never types blind into the wrong prompt (re-term's `at` verb). ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "regex": {"type": "string", "description": "POSIX extended regex to wait for before sending"},
                "text": {"type": "string", "description": "Literal text to type once the regex matches"},
                "enter": {"type": "boolean", "description": "Press Enter after text (default true)", "default": True},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "tmux key names instead of text: C-c, Enter, Up, Escape, F1 ..."},
                "timeout": {"type": "number", "description": "Seconds to wait for the guard regex (default 30)", "default": 30.0},
            },
            "required": ["session", "regex"],
        },
        "handler": tool_term_at,
    },
    "term_catch": {
        "description": "Spam a key into any shared-terminal session until the screen matches a regex, or a timeout elapses (re-term's `catch` verb, generalized past serial-console sessions). ENERGIZING — requires energize-confirm.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session": {"type": "string", "description": "Session name"},
                "regex": {"type": "string", "description": "POSIX extended regex to catch"},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "tmux key names to send each tick instead of Enter"},
                "hz": {"type": "integer", "description": "Key bursts per second (default 20)", "default": 20},
                "secs": {"type": "integer", "description": "Timeout in seconds (default 60)", "default": 60},
            },
            "required": ["session", "regex"],
        },
        "handler": tool_term_catch,
    },
}


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC request. Mirrors limpet-plugin-flashrom/mcp/server.py's
    handle_request shape so every first-party Limpet MCP server in this repo
    speaks the same minimal stdio JSON-RPC subset."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {}) or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        }}
    elif method == "tools/list":
        tools = [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]}
                  for k, v in TOOLS.items()]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools}}
    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {}) or {}
        if tool_name not in TOOLS:
            return {"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32601, "message": f"unknown tool: {tool_name}"}}
        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]}}
        except Exception as e:  # noqa: BLE001 — surface any handler failure as a tool error, not a crash
            return {"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True}}
    elif method == "notifications/initialized":
        return None  # notification, no response
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {
            "code": -32601, "message": f"method not found: {method}"}}


def main() -> None:
    """stdio JSON-RPC loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
