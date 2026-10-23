"""wq#4507 -- LIMPET agent chat TUI (Textual), readline MVP fallback.

Per docs/agent-chat-spec.md Sec2/Sec4/Sec6/Sec9: the chat UI is a thin client
over an agent session's event stream, never the agent itself. The real
limpet-session service (build order step 1, docs/agent-chat-spec.md:265) has
not landed yet, so AgentSession below accepts an injectable `backend` callable
and is honest when none is wired -- no canned LLM reply is faked, it states
plainly that no backend is attached. The event log it appends
(`notes/session.jsonl`) matches the append-only shape Sec6 specifies, and the
tool-call panel reuses workspace_console's read-only evidence tail (Sec4.2)
instead of re-deriving it.

Textual is optional (`limpet/requirements-tui.txt`): this module imports fine
without it installed, and `main()` falls back to a plain input()-loop MVP
console for headless/no-Textual sessions -- the ticket's explicit fallback
requirement -- reusing the same AgentSession so chat logging is identical
either way.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from typing import Callable, Iterable, Iterator

import workspace_console as wc

try:
    from textual.app import App, ComposeResult
    from textual.containers import VerticalScroll
    from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False


SESSION_LOG_RELPATH = os.path.join("notes", "session.jsonl")
NO_BACKEND_NOTICE = "[no agent backend wired -- limpet-session service not deployed yet]"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclasses.dataclass
class StatusInfo:
    """The status bar's three fields (ticket: model/provider/devices)."""

    model: str
    provider: str
    devices: list

    def render(self) -> str:
        devices = ", ".join(self.devices) if self.devices else "none"
        return f"model: {self.model}  provider: {self.provider}  devices: {devices}"


@dataclasses.dataclass
class ChatTurn:
    role: str  # "user" | "agent"
    text: str
    ts: str
    done: bool = True


def read_manifest(workspace: str, manifest_name: str = "manifest.json") -> dict:
    """Minimal, honest manifest read for the workspace header panel (Sec4.4).

    Returns {} (not an error) when the directory isn't a Limpet workspace yet --
    the chat TUI must still start before a target is opened (`open_target`).
    """
    path = os.path.join(workspace, manifest_name)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def git_state(workspace: str) -> dict:
    """Read-only HEAD/dirty state for the workspace header (Sec4.4). Never writes."""
    if not os.path.isdir(os.path.join(workspace, ".git")):
        return {"head": None, "dirty": None}
    try:
        head = subprocess.run(
            ["git", "-C", workspace, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", workspace, "status", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return {"head": None, "dirty": None}
    return {"head": head or None, "dirty": bool(status.strip())}


def workspace_header(workspace: str) -> dict:
    """The Sec4.4 header: target identity, bound hardware, git state."""
    man = read_manifest(workspace)
    hardware_bound = man.get("hardware_bound")
    gs = git_state(workspace)
    return {
        "target_id": man.get("target_id"),
        "identity": man.get("identity", {}),
        "hardware_bound": hardware_bound if isinstance(hardware_bound, list) else [],
        "git_head": gs["head"],
        "git_dirty": gs["dirty"],
    }


def status_from_env(workspace: str, model: str | None = None, provider: str | None = None) -> StatusInfo:
    """Model/provider are never hard-coded (Sec5): read the operator's explicit
    choice, else the environment the session was launched with. 'unset' is an
    honest default when neither is configured -- not a guessed model name."""
    header = workspace_header(workspace)
    devices = [d.get("adapter") or d.get("capability", "?") for d in header["hardware_bound"]]
    return StatusInfo(
        model=model or os.environ.get("LIMPET_MODEL", "unset"),
        provider=provider or os.environ.get("LIMPET_PROVIDER", "unset"),
        devices=devices,
    )


def tool_rows(workspace: str) -> list:
    """Tool-call panel rows (Sec4.2), reusing the read-only evidence tail as-is."""
    return [wc.project(b) for b in wc.read_blocks(workspace)]


class AgentSession:
    """A thin client's view of one agent session (Sec2/Sec6).

    `backend`, when given, is `text -> Iterable[str]`: the streamed reply
    chunks for one user turn. With no backend, the session says so honestly
    instead of fabricating a reply -- there is no limpet-session service to
    attach to yet. Every turn is also appended to `notes/session.jsonl`
    (Sec4.1 persistence) so chat history survives a reconnect, same as spec.
    """

    def __init__(self, workspace: str, backend: Callable[[str], Iterable[str]] | None = None,
                 session_log_relpath: str = SESSION_LOG_RELPATH,
                 clock: Callable[[], str] = _now_iso):
        self.workspace = workspace
        self.backend = backend
        self.turns: list = []
        self._log_relpath = session_log_relpath
        self._clock = clock

    def _log_path(self) -> str:
        return os.path.join(self.workspace, self._log_relpath)

    def _append_log(self, event: dict) -> None:
        if not os.path.isdir(self.workspace):
            return
        path = self._log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")

    def reply_stream(self, text: str) -> Iterator[str]:
        """Record the user turn, then yield the agent reply delta-by-delta."""
        ts = self._clock()
        self.turns.append(ChatTurn(role="user", text=text, ts=ts))
        self._append_log({"kind": "user_turn", "text": text, "ts": ts})

        agent_turn = ChatTurn(role="agent", text="", ts=self._clock(), done=False)
        self.turns.append(agent_turn)
        chunks = self.backend(text) if self.backend is not None else [NO_BACKEND_NOTICE]
        for chunk in chunks:
            agent_turn.text += chunk
            self._append_log({"kind": "agent_message", "text_delta": chunk, "done": False,
                               "ts": self._clock()})
            yield chunk
        agent_turn.done = True
        self._append_log({"kind": "agent_message", "text": agent_turn.text, "done": True,
                           "ts": self._clock()})


if TEXTUAL_AVAILABLE:

    class LimpetChatApp(App):
        """Sec4 layout: chat history, status bar, tool panel, streaming reply."""

        CSS = """
        #status-bar { height: 1; background: $panel; color: $text; padding: 0 1; }
        #agent-stream { height: 1; color: $text-muted; padding: 0 1; }
        #chat-pane { height: 1fr; border: round $primary; }
        #tool-table { height: 10; border: round $accent; }
        """
        BINDINGS = [("ctrl+c", "quit", "Quit")]

        def __init__(self, session: AgentSession, status: StatusInfo, **kwargs):
            super().__init__(**kwargs)
            self.session = session
            self.status = status
            self._known_tool_rows = 0

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static(self.status.render(), id="status-bar")
            with VerticalScroll(id="chat-pane"):
                yield RichLog(id="chat-log", wrap=True, markup=False)
            yield Static("", id="agent-stream")
            yield DataTable(id="tool-table")
            yield Input(placeholder="message the agent...", id="chat-input")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#tool-table", DataTable)
            table.add_columns("ts", "tool", "ok", "coverage")
            self._refresh_tools()
            self.set_interval(1.0, self._refresh_tools)
            self.query_one("#chat-input", Input).focus()

        def _refresh_tools(self) -> None:
            rows = tool_rows(self.session.workspace)
            table = self.query_one("#tool-table", DataTable)
            for row in rows[self._known_tool_rows:]:
                table.add_row(row["ts"], str(row["tool"]), "ok" if row["ok"] else "FAIL",
                              row["coverage"] or "-")
            self._known_tool_rows = len(rows)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            event.input.value = ""
            if not text:
                return
            log = self.query_one("#chat-log", RichLog)
            stream = self.query_one("#agent-stream", Static)
            log.write(f"you: {text}")
            cumulative = ""
            for chunk in self.session.reply_stream(text):
                cumulative += chunk
                stream.update(f"agent: {cumulative}")
            log.write(f"agent: {cumulative}")
            stream.update("")


def run_readline(session: AgentSession, status: StatusInfo, input_fn=input, out=None) -> None:
    """Headless/no-Textual MVP: the same AgentSession, a plain input() loop."""
    out = sys.stdout if out is None else out
    print(status.render(), file=out)
    for row in tool_rows(session.workspace):
        print(f"[tool] {row['ts']}\t{row['tool']}\t{'ok' if row['ok'] else 'FAIL'}", file=out)
    print("Type a message, or 'exit' to quit.", file=out)
    while True:
        try:
            text = input_fn("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(file=out)
            return
        if not text:
            continue
        if text in ("exit", "quit"):
            return
        out.write("agent> ")
        for chunk in session.reply_stream(text):
            out.write(chunk)
            out.flush()
        out.write("\n")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="limpet-agent-tui", description="LIMPET agent chat TUI")
    p.add_argument("--workspace", default=".", help="workspace directory (manifest.json root)")
    p.add_argument("--model", help="override the model shown in the status bar")
    p.add_argument("--provider", help="override the provider shown in the status bar")
    p.add_argument("--no-textual", action="store_true", help="force the readline MVP fallback")
    return p


def main(argv: list | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    workspace = os.path.abspath(args.workspace)
    status = status_from_env(workspace, model=args.model, provider=args.provider)
    session = AgentSession(workspace)

    use_textual = TEXTUAL_AVAILABLE and not args.no_textual and sys.stdin.isatty()
    if not use_textual:
        if not TEXTUAL_AVAILABLE and not args.no_textual:
            print("textual not installed -- falling back to the readline MVP", file=sys.stderr)
        run_readline(session, status)
        return 0

    LimpetChatApp(session, status).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
