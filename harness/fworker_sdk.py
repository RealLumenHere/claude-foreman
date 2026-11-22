#!/usr/bin/env python3
"""fworker_sdk — the anthropic-SDK agent behind fworker's `sdk` runtime (wq#5211).

WHY: Max/Ultra monthly API credits pay for Messages API / Agent SDK calls on a key
from the linked Console org, but NOT for Claude Code. Anthropic tags Claude Code
traffic by its `anthropic-beta: claude-code-*` header, and that traffic is refused
with credit_balance_too_low on the very key that serves plain Messages calls
(measured 2026-11-21). So `claude -p` on an API key cannot spend the credit. This
is a plain Messages API client (anthropic SDK -> LiteLLM /v1/messages, scoped
`foreman-apicredit` key), which can.

HOW IT FITS: a drop-in replacement for the `claude -p` child. fworker builds the
same argv (build_claude_cmd) and swaps argv[0]; this program accepts the same flags
and writes Claude Code's stream-json frames (system/init, assistant, user
tool_result, result) on stdout. So fworker.run_child() supervises it unchanged:
frame-gap/wall-clock/turn watchdogs, mask screening, heartbeats, kill-tree,
RunState accounting, exit-gate, verify_done, wq done/release.

SAFETY RAILS, the same ones the CLI worker gets:
  * --settings hooks run with Claude Code's hook contract: SessionStart,
    PreToolUse (guard.sh, action_gate.py: exit 2 / permissionDecision deny|ask|
    defer all block, updatedInput honoured), Stop (guard.sh, stop_gate.py:
    exit 2 / decision=block continues the session with the reason), SessionEnd
  * --settings permissions.allow/deny enforced Claude-Code style (deny wins;
    Bash compound commands need every segment allowed; acceptEdits = edits only
    inside cwd + --add-dir)
  * Read-before-Edit/Write, Bash timeout (default 2m, max 10m), output capped at
    BASH_MAX_OUTPUT_LENGTH, process-group kill on timeout
  * the API key is read from FWORKER_SDK_API_KEY then scrubbed from the env every
    tool subprocess inherits
  * every API call is priced and ledgered (api_credit.record); the local cycle cap
    and credit_balance_too_low stop the session (exit 3, API_CREDIT_EXHAUSTED)

Tools: Bash, Read, Write, Edit, Grep, Glob (Claude Code names, so guard.sh /
action_gate / permission rules match unchanged). git, SSH (fleet-exec) and wq go
through Bash under those same rules.

Sessions: messages persist to FWORKER_SDK_SESSIONS/<sid>.json (+ a Claude-Code-
format transcript <sid>.jsonl the Stop hook reads); --resume <sid> continues one.
SDK session ids start with "sdk-" so a resume is never routed to the CLI.
"""
import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import api_credit  # noqa: E402

SESSIONS_DIR = os.environ.get("FWORKER_SDK_SESSIONS", "/home/lumen/foreman/_runs/sdk-sessions")
DEFAULT_MAX_TOKENS = int(os.environ.get("FWORKER_SDK_MAX_TOKENS", "32000"))
COMPACT_AT_TOKENS = int(os.environ.get("FWORKER_SDK_COMPACT_AT", "400000"))
KEEP_RECENT_TOOL_RESULTS = int(os.environ.get("FWORKER_SDK_KEEP_RESULTS", "8"))
KEEPALIVE_S = 30
BASH_DEFAULT_TIMEOUT_MS = 120_000
BASH_MAX_TIMEOUT_MS = 600_000
READ_DEFAULT_LINES = 2000
READ_MAX_LINE_CHARS = 2000
MAX_STOP_BLOCKS = 5
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SECRET_ENV = ("FWORKER_SDK_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "CLAUDE_CODE_OAUTH_TOKEN")
EXIT_CREDIT = 3

_OUT_LOCK = threading.Lock()


def emit(frame):
    with _OUT_LOCK:
        sys.stdout.write(json.dumps(frame, default=str) + "\n")
        sys.stdout.flush()


def errlog(msg):
    sys.stderr.write(f"[fworker_sdk {datetime.now(timezone.utc):%H:%M:%S}] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# permission rules (Claude Code settings.permissions)
# ---------------------------------------------------------------------------

RULE_RE = re.compile(r"^([A-Za-z_][\w\-]*)(?:\((.*)\))?$", re.S)


def parse_rule(rule):
    m = RULE_RE.match(rule.strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def _bash_spec_re(spec):
    if spec.endswith(":*"):  # legacy prefix form
        return re.compile(re.escape(spec[:-2]) + r"(\s.*)?$", re.S)
    return re.compile("".join(".*" if ch == "*" else re.escape(ch) for ch in spec) + "$", re.S)


def _path_glob_re(spec):
    s = spec
    if s.startswith("~/"):
        s = os.path.expanduser(s)
    elif s.startswith("//"):
        s = s[1:]
    anchored = s.startswith("/")
    out, i = [], 0
    while i < len(s):
        if s.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif s.startswith("**", i):
            out.append(".*")
            i += 2
        elif s[i] == "*":
            out.append("[^/]*")
            i += 1
        elif s[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(s[i]))
            i += 1
    body = "".join(out)
    return re.compile(("" if anchored else "(?:.*/)?") + body + "$", re.S)


_SEP_RE = re.compile(r"\|\||&&|;|\||\n|&(?!>)")


def split_bash(cmd):
    """Top-level segments of a shell command (quote-aware), plus the bodies of $(...)
    and backtick substitutions, each also split. Conservative by design: anything we
    can't attribute to a segment stays inside one and must match a rule."""
    segs, cur, i, q = [], [], 0, None
    subs = []
    while i < len(cmd):
        ch = cmd[i]
        if q:
            cur.append(ch)
            if ch == "\\" and q == '"' and i + 1 < len(cmd):
                cur.append(cmd[i + 1])
                i += 2
                continue
            if ch == q:
                q = None
            elif q == '"' and cmd.startswith("$(", i):
                depth, j = 1, i + 2
                while j < len(cmd) and depth:
                    depth += {"(": 1, ")": -1}.get(cmd[j], 0)
                    j += 1
                subs.append(cmd[i + 2:j - 1])
                cur.append(cmd[i + 1:j])
                i = j
                continue
            i += 1
            continue
        if ch in "'\"":
            q = ch
            cur.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < len(cmd):
            cur.append(cmd[i:i + 2])
            i += 2
            continue
        if cmd.startswith("$(", i):
            depth, j = 1, i + 2
            while j < len(cmd) and depth:
                depth += {"(": 1, ")": -1}.get(cmd[j], 0)
                j += 1
            subs.append(cmd[i + 2:j - 1])
            cur.append(cmd[i:j])
            i = j
            continue
        if ch == "`":
            j = cmd.find("`", i + 1)
            j = len(cmd) if j < 0 else j
            subs.append(cmd[i + 1:j])
            cur.append(cmd[i:j + 1])
            i = j + 1
            continue
        m = None if (ch == "&" and i > 0 and cmd[i - 1] in "<>") else _SEP_RE.match(cmd, i)
        if m:
            segs.append("".join(cur))
            cur = []
            i = m.end()
            continue
        cur.append(ch)
        i += 1
    segs.append("".join(cur))
    out = []
    for s in segs:
        s = s.strip()
        # strip leading VAR=val assignments and grouping chars for matching
        s = re.sub(r"^(?:[A-Za-z_]\w*=(?:'[^']*'|\"[^\"]*\"|\S*)\s+)+", "", s)
        while s[:1] in ("(", "{"):
            close = ")" if s[0] == "(" else "}"
            s = s[1:]
            if s.endswith(close):
                s = s[:-1]
            s = s.strip()
        if s:
            out.append(s)
    for sub in subs:
        out.extend(split_bash(sub))
    return out


class Permissions:
    def __init__(self, settings, workdirs, mode):
        p = (settings or {}).get("permissions") or {}
        self.allow = [parse_rule(r) for r in p.get("allow") or []]
        self.deny = [parse_rule(r) for r in p.get("deny") or []]
        self.workdirs = [os.path.realpath(d) for d in workdirs if d]
        self.mode = mode or p.get("defaultMode") or "default"

    def _in_workdirs(self, path):
        rp = os.path.realpath(path)
        return any(rp == d or rp.startswith(d.rstrip("/") + "/") for d in self.workdirs)

    def _match(self, rules, tool, value, is_bash):
        for t, spec in rules:
            if t != tool:
                continue
            if spec is None or spec == "" or spec == "*":
                return f"{t}" if spec is None else f"{t}({spec})"
            if value is None:
                continue
            if is_bash:
                if _bash_spec_re(spec).match(value.strip()):
                    return f"{t}({spec})"
            else:
                rx = _path_glob_re(spec)
                cands = {value, os.path.realpath(value)}
                if any(rx.match(c) for c in cands):
                    return f"{t}({spec})"
        return None

    def check(self, tool, tool_input):
        """-> (allowed: bool, reason: str)"""
        if tool == "Bash":
            cmd = (tool_input.get("command") or "").strip()
            if not cmd:
                return False, "empty command"
            for value in [cmd] + split_bash(cmd):
                d = self._match(self.deny, "Bash", value, True)
                if d:
                    return False, f"denied by permission rule {d}"
            unmatched = [s for s in split_bash(cmd) if not self._match(self.allow, "Bash", s, True)]
            if unmatched:
                return False, ("not in the permission allow list (headless run, no prompt): "
                               + "; ".join(repr(s[:120]) for s in unmatched[:3]))
            return True, "allowed"
        if tool in ("Read", "Write", "Edit", "Grep", "Glob"):
            path = tool_input.get("file_path") or tool_input.get("path") or os.getcwd()
            path = os.path.abspath(path)
            rule_tool = "Read" if tool in ("Grep", "Glob") else tool
            d = self._match(self.deny, rule_tool, path, False) or (
                self._match(self.deny, "Edit", path, False) if tool == "Write" else None)
            if d:
                return False, f"denied by permission rule {d}"
            if self._match(self.allow, rule_tool, path, False):
                return True, "allowed"
            if rule_tool in ("Edit", "Write") and self.mode == "acceptEdits" \
                    and self._in_workdirs(path):
                return True, "acceptEdits (inside working directories)"
            if rule_tool == "Read" and self._in_workdirs(path):
                return True, "inside working directories"
            return False, f"{tool} on {path} is outside the working directories and not allowed"
        return False, f"unknown tool {tool}"


# ---------------------------------------------------------------------------
# hooks (Claude Code hook contract)
# ---------------------------------------------------------------------------

class Hooks:
    def __init__(self, settings, env, cwd):
        self.cfg = (settings or {}).get("hooks") or {}
        self.env = env
        self.cwd = cwd

    def run(self, event, payload, tool_name=None):
        out = []
        for entry in self.cfg.get(event) or []:
            m = entry.get("matcher")
            if tool_name is not None and m not in (None, "", "*"):
                try:
                    if not re.fullmatch(m, tool_name):
                        continue
                except re.error:
                    continue
            for h in entry.get("hooks") or []:
                if h.get("type") != "command" or not h.get("command"):
                    continue
                try:
                    p = subprocess.run(h["command"], shell=True, input=json.dumps(payload),
                                       capture_output=True, text=True, cwd=self.cwd,
                                       env=self.env, timeout=int(h.get("timeout", 120)))
                    out.append((h["command"], p.returncode, p.stdout, p.stderr))
                except subprocess.TimeoutExpired:
                    out.append((h["command"], 124, "", "hook timed out"))
                except Exception as e:  # a hook that cannot run must not wave a call through
                    out.append((h["command"], 2, "", f"hook failed to run: {e}"))
        return out

    @staticmethod
    def _json(stdout):
        s = (stdout or "").strip()
        if not s.startswith("{"):
            return None
        try:
            return json.loads(s)
        except ValueError:
            return None

    def pre_tool(self, payload, tool_name):
        """-> (allowed, reason, updated_input)"""
        updated = None
        for cmd, rc, so, se in self.run("PreToolUse", payload, tool_name):
            if rc == 2:
                return False, (se or so).strip() or f"blocked by hook {cmd}", None
            j = self._json(so)
            if j:
                hso = j.get("hookSpecificOutput") or {}
                dec = hso.get("permissionDecision")
                reason = hso.get("permissionDecisionReason") or j.get("reason") or ""
                if dec in ("deny", "ask"):
                    return False, reason or f"{dec} from hook {cmd}", None
                if dec == "defer":
                    return False, ("deferred by hook (grant required): " + reason +
                                   " — the SDK runtime cannot hold a deferred call; "
                                   "park or block the ticket per the worker prompt"), None
                if j.get("decision") == "block":
                    return False, reason or f"blocked by hook {cmd}", None
                if isinstance(hso.get("updatedInput"), dict):
                    updated = hso["updatedInput"]
            if rc not in (0, 2):
                errlog(f"PreToolUse hook {cmd} exited {rc} (non-blocking): {se.strip()[:300]}")
        return True, "", updated

    def stop(self, payload):
        """-> block reason or None"""
        reasons = []
        for cmd, rc, so, se in self.run("Stop", payload):
            j = self._json(so)
            if rc == 2:
                reasons.append((se or so).strip() or f"stop blocked by {cmd}")
            elif j and j.get("decision") == "block":
                reasons.append(j.get("reason") or f"stop blocked by {cmd}")
        return "\n\n".join(reasons) if reasons else None

    def context(self, event, payload):
        ctx = []
        for cmd, rc, so, se in self.run(event, payload):
            j = self._json(so)
            if j:
                a = (j.get("hookSpecificOutput") or {}).get("additionalContext")
                if a:
                    ctx.append(a)
            elif rc == 0 and so.strip():
                ctx.append(so.strip())
            if rc == 2:
                errlog(f"{event} hook {cmd} exited 2: {se.strip()[:300]}")
        return "\n".join(ctx)


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {"name": "Bash",
     "description": (
         "Run a bash command and return its combined stdout/stderr. The working directory "
         "persists between calls (a `cd` carries over). Default timeout 120000 ms, max 600000. "
         "Output beyond the cap is truncated in the middle, so filter large output (grep, head, "
         "tail, wc). Use for git, tests, builds, wq, lumen-pr, and fleet nodes via "
         "/home/lumen/bin/fleet-exec. Commands are checked against the run's permission rules "
         "and guard hooks; a denial is final for that exact command, so change approach."),
     "input_schema": {"type": "object", "properties": {
         "command": {"type": "string", "description": "The command to run"},
         "timeout": {"type": "integer", "description": "Timeout in ms (max 600000)"},
         "description": {"type": "string", "description": "5-10 word summary of the command"}},
         "required": ["command"]}},
    {"name": "Read",
     "description": (
         "Read a text file by absolute path. Returns `cat -n` style lines (line numbers start "
         "at 1). Default up to 2000 lines; use offset/limit for long files. Lines over 2000 "
         "chars are truncated. A file must be Read before it can be edited or overwritten."),
     "input_schema": {"type": "object", "properties": {
         "file_path": {"type": "string", "description": "Absolute path"},
         "offset": {"type": "integer", "description": "1-based line to start from"},
         "limit": {"type": "integer", "description": "Number of lines to read"}},
         "required": ["file_path"]}},
    {"name": "Write",
     "description": (
         "Write a file (absolute path), creating parent directories. Overwrites an existing "
         "file, which must have been Read first. Prefer Edit for changes to existing files."),
     "input_schema": {"type": "object", "properties": {
         "file_path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["file_path", "content"]}},
    {"name": "Edit",
     "description": (
         "Replace an exact string in a file that was Read in this session. old_string must "
         "match exactly (including indentation) and be unique unless replace_all is true. "
         "Do not include the Read line-number prefix in old_string."),
     "input_schema": {"type": "object", "properties": {
         "file_path": {"type": "string"}, "old_string": {"type": "string"},
         "new_string": {"type": "string"},
         "replace_all": {"type": "boolean", "default": False}},
         "required": ["file_path", "old_string", "new_string"]}},
    {"name": "Grep",
     "description": (
         "Search file contents with ripgrep. output_mode: files_with_matches (default), "
         "content (matching lines, with -n line numbers), or count. Filter with glob "
         "(e.g. '*.py') or type (e.g. 'py')."),
     "input_schema": {"type": "object", "properties": {
         "pattern": {"type": "string", "description": "Regex"},
         "path": {"type": "string", "description": "File or directory (default cwd)"},
         "glob": {"type": "string"}, "type": {"type": "string"},
         "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
         "-i": {"type": "boolean"}, "-n": {"type": "boolean"},
         "-A": {"type": "integer"}, "-B": {"type": "integer"}, "-C": {"type": "integer"},
         "multiline": {"type": "boolean"},
         "head_limit": {"type": "integer", "description": "Max output lines/entries"}},
         "required": ["pattern"]}},
    {"name": "Glob",
     "description": "Find files by glob pattern (e.g. '**/*.py'), newest first, max 200.",
     "input_schema": {"type": "object", "properties": {
         "pattern": {"type": "string"},
         "path": {"type": "string", "description": "Directory to search (default cwd)"}},
         "required": ["pattern"]}},
]


def _cap(text, limit):
    if limit <= 0 or len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return (text[:head] + f"\n\n... [{len(text) - limit} characters truncated] ...\n\n"
            + text[-tail:])


class ToolBox:
    def __init__(self, cwd, env):
        self.cwd = cwd
        self.shell_cwd = cwd
        self.env = env
        self.read_files = set()
        self.current = None  # running Bash Popen, killed with the session (own pgid)
        self.on_tick = None  # called every KEEPALIVE_S while a tool process runs
        self.out_cap = int(env.get("BASH_MAX_OUTPUT_LENGTH") or 30000)

    def run(self, name, inp):
        """-> (text, is_error)"""
        fn = getattr(self, "t_" + name.lower(), None)
        if not fn:
            return f"Unknown tool {name}", True
        try:
            return fn(inp or {})
        except Exception as e:
            return f"{name} failed: {type(e).__name__}: {e}", True

    def t_bash(self, inp):
        cmd = inp.get("command") or ""
        if inp.get("run_in_background"):
            return ("run_in_background is not supported in the SDK runtime; run the "
                    "command in the foreground with a timeout"), True
        t_ms = int(inp.get("timeout") or BASH_DEFAULT_TIMEOUT_MS)
        t_ms = max(1000, min(t_ms, BASH_MAX_TIMEOUT_MS))
        fd, pwd_file = tempfile.mkstemp(prefix="fwsdk-pwd-", dir=self.env.get("TMPDIR") or None)
        os.close(fd)
        script = ('cd -- "$1" 2>/dev/null || cd -- "$2"; eval "$3"; __rc=$?; '
                  'pwd > "$4" 2>/dev/null; exit $__rc')
        start_cwd = self.shell_cwd if os.path.isdir(self.shell_cwd) else self.cwd
        p = subprocess.Popen(["bash", "-c", script, "fwsdk", start_cwd, self.cwd, cmd, pwd_file],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=self.env, cwd=start_cwd,
                             start_new_session=True)
        self.current = p
        timed_out = False
        chunks = []
        reader = threading.Thread(target=lambda: chunks.append(p.stdout.read()), daemon=True)
        reader.start()
        deadline = time.time() + t_ms / 1000
        try:
            while True:
                try:
                    p.wait(timeout=max(0.1, min(KEEPALIVE_S, deadline - time.time())))
                    break
                except subprocess.TimeoutExpired:
                    if time.time() >= deadline:
                        timed_out = True
                        for sig in (signal.SIGTERM, signal.SIGKILL):
                            try:
                                os.killpg(p.pid, sig)
                                p.wait(timeout=5)
                                break
                            except Exception:
                                pass
                        break
                    # a live, bounded tool process is not a stalled session: tell fworker's
                    # frame-gap watchdog so (Claude Code also reports during long tools)
                    if self.on_tick:
                        self.on_tick()
        finally:
            self.current = None if timed_out or p.poll() is not None else self.current
        reader.join(timeout=10)
        out = chunks[0] if chunks else b""
        try:
            with open(pwd_file) as f:
                nc = f.read().strip()
            if nc and os.path.isdir(nc):
                self.shell_cwd = nc
        except OSError:
            pass
        finally:
            try:
                os.unlink(pwd_file)
            except OSError:
                pass
        text = _cap((out or b"").decode("utf-8", "replace").rstrip("\n"), self.out_cap)
        if timed_out:
            return (text + f"\n\nCommand timed out after {t_ms} ms").strip(), True
        if p.returncode != 0:
            return (text + f"\n\nExit code {p.returncode}").strip(), True
        return text or "(no output)", False

    def _abs(self, path):
        if not path:
            raise ValueError("file_path is required")
        if not os.path.isabs(path):
            raise ValueError(f"file_path must be absolute, got {path!r}")
        return os.path.normpath(path)

    def t_read(self, inp):
        path = self._abs(inp.get("file_path"))
        if os.path.isdir(path):
            return f"{path} is a directory; use Bash ls or Glob", True
        if not os.path.exists(path):
            return f"File does not exist: {path}", True
        with open(path, "rb") as f:
            raw = f.read()
        if b"\0" in raw[:8192]:
            return f"{path} looks binary ({len(raw)} bytes); not shown", True
        lines = raw.decode("utf-8", "replace").splitlines()
        self.read_files.add(os.path.realpath(path))
        if not lines:
            return "<system-reminder>The file exists but is empty.</system-reminder>", False
        off = max(1, int(inp.get("offset") or 1))
        lim = max(1, int(inp.get("limit") or READ_DEFAULT_LINES))
        chunk = lines[off - 1: off - 1 + lim]
        body = "\n".join(f"{i:>6}\t{(l[:READ_MAX_LINE_CHARS] + '...[truncated]') if len(l) > READ_MAX_LINE_CHARS else l}"
                         for i, l in enumerate(chunk, start=off))
        if off - 1 + lim < len(lines):
            body += f"\n\n[{len(lines) - (off - 1 + lim)} more lines; use offset/limit]"
        return body, False

    def t_write(self, inp):
        path = self._abs(inp.get("file_path"))
        if os.path.exists(path) and os.path.realpath(path) not in self.read_files:
            return "File has not been read yet. Read it first before writing to it.", True
        os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
        content = inp.get("content")
        if content is None:
            return "content is required", True
        existed = os.path.exists(path)
        with open(path, "w") as f:
            f.write(content)
        self.read_files.add(os.path.realpath(path))
        return f"{'Updated' if existed else 'Created'} {path} ({len(content)} chars)", False

    def t_edit(self, inp):
        path = self._abs(inp.get("file_path"))
        if not os.path.exists(path):
            return f"File does not exist: {path}", True
        if os.path.realpath(path) not in self.read_files:
            return "File has not been read yet. Read it first before editing it.", True
        old, new = inp.get("old_string"), inp.get("new_string")
        if old is None or new is None:
            return "old_string and new_string are required", True
        if old == new:
            return "old_string and new_string are identical; no change", True
        with open(path) as f:
            text = f.read()
        n = text.count(old) if old else 0
        if n == 0:
            return "String to replace not found in file (match must be exact).", True
        if n > 1 and not inp.get("replace_all"):
            return (f"Found {n} matches of old_string; add surrounding context to make it "
                    f"unique, or set replace_all=true."), True
        text = text.replace(old, new) if inp.get("replace_all") else text.replace(old, new, 1)
        with open(path, "w") as f:
            f.write(text)
        idx = text.find(new)
        ln = text.count("\n", 0, idx) + 1 if idx >= 0 else 1
        snippet = text.splitlines()[max(0, ln - 4): ln + new.count("\n") + 3]
        shown = "\n".join(f"{i:>6}\t{l}" for i, l in enumerate(snippet, start=max(1, ln - 3)))
        return f"Edited {path} ({n if inp.get('replace_all') else 1} replacement):\n{shown}", False

    def t_grep(self, inp):
        mode = inp.get("output_mode") or "files_with_matches"
        argv = ["rg", "--no-messages", "--color=never"]
        if mode == "files_with_matches":
            argv.append("-l")
        elif mode == "count":
            argv.append("-c")
        else:
            if inp.get("-n", True):
                argv.append("-n")
            for k in ("-A", "-B", "-C"):
                if inp.get(k) is not None:
                    argv += [k, str(int(inp[k]))]
        if inp.get("-i"):
            argv.append("-i")
        if inp.get("multiline"):
            argv += ["-U", "--multiline-dotall"]
        if inp.get("glob"):
            argv += ["--glob", inp["glob"]]
        if inp.get("type"):
            argv += ["--type", inp["type"]]
        argv += ["-e", inp.get("pattern") or "", "--", inp.get("path") or self.shell_cwd]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=self.env,
                           cwd=self.shell_cwd)
        if p.returncode not in (0, 1):
            return f"rg error: {p.stderr.strip()[:500]}", True
        lines = p.stdout.splitlines()
        hl = int(inp.get("head_limit") or 0)
        if hl > 0:
            lines = lines[:hl]
        return _cap("\n".join(lines), self.out_cap * 5) or "No matches found", False

    def t_glob(self, inp):
        base = Path(inp.get("path") or self.shell_cwd)
        pat = inp.get("pattern") or ""
        if not pat:
            return "pattern is required", True
        hits = [p for p in base.glob(pat) if p.is_file()]
        hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        more = len(hits) - 200
        txt = "\n".join(str(p) for p in hits[:200]) or "No files found"
        return txt + (f"\n[{more} more not shown]" if more > 0 else ""), False


# ---------------------------------------------------------------------------
# session + prompt
# ---------------------------------------------------------------------------

def load_claude_md(cwd):
    """Claude Code's memory files: ~/.claude/CLAUDE.md, then CLAUDE.md from / down to cwd."""
    found = []
    user = os.path.expanduser("~/.claude/CLAUDE.md")
    if os.path.isfile(user):
        found.append(user)
    parts = Path(os.path.realpath(cwd)).parts
    for i in range(1, len(parts) + 1):
        p = os.path.join(*parts[:i], "CLAUDE.md")
        if os.path.isfile(p) and p not in found:
            found.append(p)
    out = []
    for p in found:
        try:
            with open(p) as f:
                out.append(f'<claude_md path="{p}">\n{f.read()[:60000]}\n</claude_md>')
        except OSError:
            pass
    return "\n\n".join(out)


def build_system(cwd, add_dirs, model, out_cap):
    now = datetime.now(timezone.utc).astimezone()
    sysmsg = f"""You are an autonomous software engineering agent running headless inside Lumen's foreman harness (fworker SDK runtime, wq#5211, model {model}). No human watches this run and nobody can answer questions mid-run: act on the task, and when a decision truly needs Frank, follow the worker prompt's park/block protocol instead of asking.

<env>
Working directory: {cwd}
Additional readable directories: {', '.join(add_dirs) or 'none'}
Platform: linux. Date: {now:%Y-%m-%d %H:%M %Z}.
</env>

Tools: Bash, Read, Write, Edit, Grep, Glob.
- Prefer Read/Edit/Write/Grep/Glob over cat/sed/echo-redirects for file work. Read a file before editing or overwriting it; Edit's old_string must match exactly and be unique.
- Bash output is truncated past {out_cap} characters, so narrow it with grep/head/tail/wc. Commands time out at 2 minutes by default (max 10 minutes via the timeout parameter); no background jobs.
- Every tool call is checked by the run's permission rules and guard hooks. A denial is final for that exact call: do not retry it reworded or try to get around it. Use an allowed path, or record the blocker the way the worker prompt says.
- Use absolute paths. Fleet nodes are reached through /home/lumen/bin/fleet-exec, not raw ssh.
- Be economical: batch independent reads, avoid re-reading files you already have, keep reasoning focused.
- Your final message must satisfy the worker prompt's exit contract (its EXIT_SIGNAL/sentinel lines) exactly."""
    md = load_claude_md(cwd)
    if md:
        sysmsg += ("\n\nProject instructions (CLAUDE.md files) follow. They override defaults; "
                   "the worker prompt in the first user message overrides them.\n\n" + md)
    return sysmsg


class Session:
    def __init__(self, sid, model, cwd, system, messages=None):
        self.sid, self.model, self.cwd, self.system = sid, model, cwd, system
        self.messages = messages or []
        os.makedirs(SESSIONS_DIR, mode=0o700, exist_ok=True)
        self.path = os.path.join(SESSIONS_DIR, f"{sid}.json")
        self.transcript = os.path.join(SESSIONS_DIR, f"{sid}.jsonl")

    @classmethod
    def load(cls, sid):
        p = os.path.join(SESSIONS_DIR, f"{sid}.json")
        with open(p) as f:
            d = json.load(f)
        return cls(sid, d["model"], d["cwd"], d["system"], d["messages"])

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"model": self.model, "cwd": self.cwd, "system": self.system,
                       "messages": self.messages}, f)
        os.replace(tmp, self.path)

    def log(self, role, message):
        with open(self.transcript, "a") as f:
            f.write(json.dumps({"type": role, "message": message, "sessionId": self.sid,
                                "uuid": str(uuid.uuid4()), "cwd": self.cwd,
                                "timestamp": datetime.now(timezone.utc).isoformat()},
                               default=str) + "\n")


def _strip_cache(messages):
    for m in messages:
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)


def _mark_cache(messages):
    """Rolling breakpoints on the last block of the last two user turns (system carries a
    third): each request reads the previous request's prefix from cache."""
    _strip_cache(messages)
    marked = 0
    for m in reversed(messages):
        if marked >= 2:
            break
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = c = [{"type": "text", "text": c}]
        for b in reversed(c):
            if isinstance(b, dict) and b.get("type") in ("text", "tool_result", "image"):
                b["cache_control"] = {"type": "ephemeral"}
                marked += 1
                break


def repair_dangling(messages):
    """A session killed mid-tool (watchdog, SIGTERM) is saved ending in an assistant turn whose
    tool_use blocks never got a tool_result; the API refuses to continue from that shape.
    Close each with an error result so a resume can carry on. Returns how many were closed."""
    if not messages or messages[-1].get("role") != "assistant":
        return 0
    c = messages[-1].get("content")
    uses = [b for b in (c if isinstance(c, list) else []) if isinstance(b, dict) and b.get("type") == "tool_use"]
    if not uses:
        return 0
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": b["id"], "is_error": True,
         "content": ("Interrupted: the session was stopped while this tool call was running; "
                     "no result was captured. Re-check its effect before relying on it.")}
        for b in uses]})
    return len(uses)


def compact(messages, keep=KEEP_RECENT_TOOL_RESULTS):
    """Elide the bodies of older tool results (oldest first), keeping the last `keep`
    intact. Message structure (tool_use/tool_result pairing) is preserved."""
    results = [b for m in messages if m.get("role") == "user" and isinstance(m.get("content"), list)
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    elided = 0
    for b in results[:-keep] if keep else results:
        c = b.get("content")
        size = len(json.dumps(c)) if c is not None else 0
        if size > 400 and not str(c).startswith("[elided"):
            b["content"] = f"[elided by fworker_sdk context manager: {size} chars of older tool output]"
            elided += 1
    return elided


# ---------------------------------------------------------------------------
# the agent loop
# ---------------------------------------------------------------------------

def _usage_dict(u):
    if u is None:
        return {}
    d = u.model_dump() if hasattr(u, "model_dump") else dict(u)
    return {k: d.get(k) or 0 for k in ("input_tokens", "output_tokens",
                                        "cache_read_input_tokens", "cache_creation_input_tokens")}


class Agent:
    def __init__(self, args, settings):
        self.args = args
        self.t0 = time.time()
        self.api_ms = 0
        self.model = args.model or api_credit.load_config().get("sdk_model")
        self.effort = args.effort if args.effort in EFFORTS else None
        self.cwd = os.getcwd()
        self.add_dirs = [os.path.abspath(d) for d in (args.add_dir or [])]
        key = os.environ.get("FWORKER_SDK_API_KEY")
        if not key:
            kf = api_credit.load_config().get("key_file")
            with open(kf) as f:
                key = f.read().strip()
        self.base_url = os.environ.get("FWORKER_SDK_BASE_URL") or api_credit.load_config().get("base_url")
        self.tool_env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV
                         and not k.startswith("FWORKER_SDK_")}
        import anthropic
        self.anthropic = anthropic
        # float timeout: anthropic>=1.17 ships its own httpx fork and rejects httpx.Timeout
        self.client = anthropic.Anthropic(base_url=self.base_url, api_key=key, max_retries=4,
                                          timeout=900.0)
        for k in SECRET_ENV:
            os.environ.pop(k, None)
        self.perms = Permissions(settings, [self.cwd] + self.add_dirs, args.permission_mode)
        self.hooks = Hooks(settings, self.tool_env, self.cwd)
        self.tools = ToolBox(self.cwd, self.tool_env)
        self._sid = None
        self.tools.on_tick = lambda: emit({"type": "stream_event", "session_id": self._sid,
                                           "event": {"type": "keepalive", "reason": "tool_running"}})
        self.usage_tot = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}
        self.cost = 0.0
        self.turns = 0
        self.denials = []
        self.ticket = os.environ.get("FOREMAN_TICKET_ID")
        self.attempt = os.environ.get("FOREMAN_ATTEMPT")

    # -- api -----------------------------------------------------------------
    def call_model(self, session):
        _mark_cache(session.messages)
        system = [{"type": "text", "text": session.system, "cache_control": {"type": "ephemeral"}}]
        extra = {"output_config": {"effort": self.effort}} if self.effort else None
        last_ping = time.time()
        t = time.time()
        with self.client.messages.stream(model=self.model, max_tokens=DEFAULT_MAX_TOKENS,
                                         system=system, tools=TOOL_DEFS,
                                         messages=session.messages, extra_body=extra) as stream:
            for _ev in stream:
                if time.time() - last_ping >= KEEPALIVE_S:
                    # proves the backend is producing tokens; keeps fworker's frame-gap
                    # watchdog honest during long thinking/output without faking liveness
                    emit({"type": "stream_event", "event": {"type": "keepalive"},
                          "session_id": session.sid})
                    last_ping = time.time()
            msg = stream.get_final_message()
        self.api_ms += int((time.time() - t) * 1000)
        _strip_cache(session.messages)
        return msg

    def account(self, session, msg):
        u = _usage_dict(msg.usage)
        for k in self.usage_tot:
            self.usage_tot[k] += int(u.get(k) or 0)
        rec = api_credit.record(self.model, u, ticket=self.ticket, session=session.sid,
                                attempt=self.attempt, request_id=getattr(msg, "id", None))
        self.cost += rec["cost"]
        return u, rec

    # -- tools ---------------------------------------------------------------
    def exec_tool(self, session, block):
        name, inp, tid = block["name"], dict(block.get("input") or {}), block["id"]
        payload = {"session_id": session.sid, "transcript_path": session.transcript,
                   "cwd": self.tools.shell_cwd, "permission_mode": self.perms.mode,
                   "hook_event_name": "PreToolUse", "tool_name": name, "tool_input": inp,
                   "tool_use_id": tid}
        ok, reason, updated = self.hooks.pre_tool(payload, name)
        if ok and updated:
            inp = {**inp, **updated}
        if ok:
            ok, reason = self.perms.check(name, inp)
        if not ok:
            self.denials.append({"tool_name": name, "tool_use_id": tid, "reason": reason[:300]})
            return {"type": "tool_result", "tool_use_id": tid, "is_error": True,
                    "content": f"Permission denied: {reason}"}
        text, is_err = self.tools.run(name, inp)
        res = {"type": "tool_result", "tool_use_id": tid, "content": text}
        if is_err:
            res["is_error"] = True
        return res

    # -- loop ----------------------------------------------------------------
    def result(self, session, subtype, text, is_error):
        emit({"type": "result", "subtype": subtype, "is_error": is_error,
              "duration_ms": int((time.time() - self.t0) * 1000), "duration_api_ms": self.api_ms,
              "num_turns": self.turns, "result": text, "session_id": session.sid,
              "total_cost_usd": round(self.cost, 6), "usage": self.usage_tot,
              "modelUsage": {self.model: {
                  "inputTokens": self.usage_tot["input_tokens"],
                  "outputTokens": self.usage_tot["output_tokens"],
                  "cacheReadInputTokens": self.usage_tot["cache_read_input_tokens"],
                  "cacheCreationInputTokens": self.usage_tot["cache_creation_input_tokens"],
                  "costUSD": round(self.cost, 6)}},
              "permission_denials": self.denials[-50:], "runtime": "sdk"})

    def run(self, prompt, resume):
        if resume:
            session = Session.load(resume)
            self.model = self.args.model or session.model
            source = "resume"
        else:
            sid = "sdk-" + uuid.uuid4().hex
            out_cap = self.tools.out_cap
            session = Session(sid, self.model, self.cwd,
                              build_system(self.cwd, self.add_dirs, self.model, out_cap))
            source = "startup"
        self._sid = session.sid
        if resume:
            n = repair_dangling(session.messages)
            if n:
                errlog(f"resume: closed {n} interrupted tool call(s) left by the previous run")
        base = {"session_id": session.sid, "transcript_path": session.transcript, "cwd": self.cwd}
        ctx = self.hooks.context("SessionStart", {**base, "hook_event_name": "SessionStart",
                                                  "source": source})
        emit({"type": "system", "subtype": "init", "session_id": session.sid, "cwd": self.cwd,
              "model": self.model, "tools": [t["name"] for t in TOOL_DEFS], "mcp_servers": [],
              "permissionMode": self.perms.mode, "apiKeySource": "FWORKER_SDK_API_KEY",
              "runtime": "sdk", "effort": self.effort})
        if prompt:
            text = prompt if not ctx else f"{prompt}\n\n<session-start-hook>\n{ctx}\n</session-start-hook>"
            block = {"type": "text", "text": text}
            last = session.messages[-1] if session.messages else None
            if last and last.get("role") == "user":
                # resume after tool results (or a repair): same user turn, roles must alternate
                if isinstance(last.get("content"), str):
                    last["content"] = [{"type": "text", "text": last["content"]}]
                last["content"].append(block)
                session.log("user", {"role": "user", "content": [block]})
            else:
                um = {"role": "user", "content": [block]}
                session.messages.append(um)
                session.log("user", um)
        elif not session.messages:
            self.result(session, "error_during_execution", "no prompt and nothing to resume", True)
            return 1
        session.save()
        stop_blocks = 0
        exit_code = 0
        try:
            while True:
                ok, why = api_credit.available()
                if not ok:
                    if "exhausted" in why or "local cap" in why:
                        api_credit.mark_exhausted(why)
                    self.result(session, "error_during_execution",
                                f"API_CREDIT_EXHAUSTED: {why}", True)
                    return EXIT_CREDIT
                if self.args.max_turns and self.turns >= self.args.max_turns:
                    self.result(session, "error_max_turns",
                                f"Reached max turns ({self.args.max_turns})", True)
                    return 1
                try:
                    msg = self.call_model(session)
                except self.anthropic.APIStatusError as e:
                    body = str(getattr(e, "message", "")) + " " + str(getattr(e, "body", ""))
                    if api_credit.is_credit_exhausted_error(body):
                        api_credit.mark_exhausted(f"API {e.status_code}: credit balance too low / budget")
                        self.result(session, "error_during_execution",
                                    "API_CREDIT_EXHAUSTED: Credit balance is too low", True)
                        return EXIT_CREDIT
                    if e.status_code == 400 and re.search(r"too long|context|max.*tokens", body, re.I) \
                            and compact(session.messages, keep=2):
                        errlog("context overflow: compacted, retrying")
                        continue
                    self.result(session, "error_during_execution",
                                f"API error {e.status_code}: {body.strip()[:800]}", True)
                    return 1
                except self.anthropic.APIConnectionError as e:
                    self.result(session, "error_during_execution",
                                f"API connection error: {e}", True)
                    return 1
                self.turns += 1
                u, rec = self.account(session, msg)
                blocks = [b.model_dump(exclude_none=True) for b in msg.content]
                am = {"role": "assistant", "content": blocks}
                session.messages.append(am)
                session.log("assistant", am)
                emit({"type": "assistant", "session_id": session.sid, "parent_tool_use_id": None,
                      "message": {"id": msg.id, "type": "message", "role": "assistant",
                                  "model": self.model, "content": blocks,
                                  "stop_reason": msg.stop_reason, "usage": u}})
                tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
                if tool_uses:
                    results = [self.exec_tool(session, b) for b in tool_uses]
                    um = {"role": "user", "content": results}
                    session.messages.append(um)
                    session.log("user", um)
                    emit({"type": "user", "session_id": session.sid, "parent_tool_use_id": None,
                          "message": um})
                    total_in = u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]
                    if total_in > COMPACT_AT_TOKENS:
                        n = compact(session.messages)
                        if n:
                            emit({"type": "system", "subtype": "compact_boundary",
                                  "session_id": session.sid,
                                  "compact_metadata": {"trigger": "auto", "pre_tokens": total_in,
                                                       "elided_tool_results": n}})
                    session.save()
                    continue
                if msg.stop_reason == "max_tokens":
                    um = {"role": "user", "content": [{"type": "text", "text":
                          "Your last reply hit the output token limit. Continue, more concisely."}]}
                    session.messages.append(um)
                    session.save()
                    continue
                final = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                block = self.hooks.stop({**base, "permission_mode": self.perms.mode,
                                         "hook_event_name": "Stop",
                                         "stop_hook_active": stop_blocks > 0})
                if block and stop_blocks < MAX_STOP_BLOCKS:
                    stop_blocks += 1
                    um = {"role": "user", "content": [{"type": "text",
                          "text": f"Stop hook feedback:\n{block}"}]}
                    session.messages.append(um)
                    session.log("user", um)
                    emit({"type": "user", "session_id": session.sid, "parent_tool_use_id": None,
                          "message": um})
                    session.save()
                    continue
                session.save()
                self.result(session, "success", final, False)
                return exit_code
        except KeyboardInterrupt:
            self.result(session, "error_during_execution", "interrupted", True)
            return 130
        finally:
            cur = self.tools.current
            if cur is not None and cur.poll() is None:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(cur.pid, sig)
                        cur.wait(timeout=5)
                        break
                    except Exception:
                        pass
            session.save()
            self.hooks.run("SessionEnd", {**base, "hook_event_name": "SessionEnd",
                                          "reason": "other"})


def parse_args(argv):
    ap = argparse.ArgumentParser(description="claude -p compatible anthropic-SDK agent (wq#5211)")
    ap.add_argument("-p", "--print", dest="prompt", nargs="?", const="", default=None)
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--settings")
    ap.add_argument("--permission-mode")
    ap.add_argument("--add-dir", action="append")
    ap.add_argument("--resume")
    ap.add_argument("--output-format", default="stream-json")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--include-hook-events", action="store_true")
    ap.add_argument("--mcp-config")
    ap.add_argument("--strict-mcp-config", action="store_true")
    ap.add_argument("--tools")
    args, unknown = ap.parse_known_args(argv)
    return args, unknown


def main(argv=None):
    args, unknown = parse_args(sys.argv[1:] if argv is None else argv)
    if unknown:
        errlog(f"ignoring unsupported flags: {unknown}")
    if args.mcp_config:
        errlog("MCP servers are not available in the SDK runtime (--mcp-config ignored)")
    settings = {}
    if args.settings:
        with open(args.settings) as f:
            settings = json.load(f)
    agent = Agent(args, settings)
    return agent.run(args.prompt or None, args.resume)


def _on_sigterm(signum, frame):
    raise KeyboardInterrupt()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _on_sigterm)
    sys.exit(main())
