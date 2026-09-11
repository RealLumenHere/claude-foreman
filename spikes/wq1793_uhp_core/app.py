"""wq#1793 spike: UHP Core S-04/S-09/C-03 over `claude -p`.

<=300 line FastAPI spike per PHASE0_WIRING_SPEC RECOMMENDATION:
3 concurrent `claude -p` sessions, gapless progressive SSE, killpg cancel.
Reuses harness.fworker.build_env / build_claude_cmd verbatim — this file does
NOT reimplement or fork fworker's run_child supervisor loop; the reader thread
below is a minimal line-forwarder only, not a watchdog.

NOTE (wq#1793, recorded in kb/ISSUE-LOG.md #328): the ticket's
projects/de-spaghettifier/decision/PHASE0_WIRING_SPEC.md and the
`uhp-conformance` CLI it names are not reachable from this worker's allowed
directories (projects/de-spaghettifier is not in add_dirs), so the S-04/S-09/
C-03 conformance suite could not be run against litellm-mechanical or
claude-max from here. This spike implements the wiring the ticket describes
inline; running the suite is unblocked once the tool/spec dirs are added.
"""
import asyncio
import os
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness"))
from fworker import build_claude_cmd, build_env  # noqa: E402  (reused, not forked)

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

MAX_CONCURRENT_SESSIONS = 3

app = FastAPI(title="wq1793-uhp-core-spike")
SESSIONS: dict[str, "Session"] = {}
SESSIONS_LOCK = threading.Lock()
SESSION_SEM = threading.Semaphore(MAX_CONCURRENT_SESSIONS)


class Session:
    """One `claude -p` child in its own process group (killpg cancel target).

    `events` is an append-only replay buffer under `lock`: every SSE
    subscriber — whenever it connects — starts at index 0, so no subscriber
    ever sees a gap relative to session start ("gapless progressive SSE").
    """

    def __init__(self, session_id: str, cmd: list[str], cwd: str, env: dict):
        self.id = session_id
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.events: list[str] = []
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.proc: Optional[subprocess.Popen] = None
        self.pgid: Optional[int] = None
        self.cancelled = False

    def start(self) -> None:
        # start_new_session=True == its own pgid, matching fworker.run_child's
        # own detach discipline — the same reason killpg is safe to use below.
        self.proc = subprocess.Popen(
            self.cmd, cwd=self.cwd, env=self.env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True,
        )
        self.pgid = os.getpgid(self.proc.pid)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        try:
            for line in iter(self.proc.stdout.readline, ""):
                with self.lock:
                    self.events.append(line)
        finally:
            self.proc.stdout.close()
            self.proc.wait()
            self.done.set()
            SESSION_SEM.release()

    def cancel(self) -> None:
        if self.pgid is None or self.done.is_set():
            return
        self.cancelled = True
        try:
            os.killpg(self.pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass


class StartRequest(BaseModel):
    prompt: str
    worktree: str
    ticket_id: str
    settings_file: str
    model: Optional[str] = None
    effort: Optional[str] = None
    add_dirs: list[str] = []


class StartResponse(BaseModel):
    session_id: str


@app.post("/sessions", response_model=StartResponse)
def start_session(req: StartRequest) -> StartResponse:
    if not SESSION_SEM.acquire(blocking=False):
        raise HTTPException(
            429, f"max {MAX_CONCURRENT_SESSIONS} concurrent sessions in flight")

    session_id = uuid.uuid4().hex
    worker_id = f"wq1793-spike-{session_id[:8]}"
    try:
        env = build_env(worker_id, req.model, req.effort, req.worktree, req.ticket_id)
        cmd = build_claude_cmd(req.prompt, req.model, req.effort,
                                req.settings_file, req.add_dirs)
        sess = Session(session_id, cmd, req.worktree, env)
        sess.start()
    except Exception:
        SESSION_SEM.release()
        raise

    with SESSIONS_LOCK:
        SESSIONS[session_id] = sess
    return StartResponse(session_id=session_id)


@app.get("/sessions/{session_id}/events")
async def stream_events(session_id: str) -> StreamingResponse:
    sess = SESSIONS.get(session_id)
    if sess is None:
        raise HTTPException(404, "unknown session")

    async def gen():
        sent = 0
        while True:
            with sess.lock:
                pending = sess.events[sent:]
                sent = len(sess.events)
            for line in pending:
                yield f"data: {line.rstrip(chr(10))}\n\n"
            if sess.done.is_set() and sent >= len(sess.events):
                reason = "cancelled" if sess.cancelled else "complete"
                yield f'event: end\ndata: {{"reason": "{reason}"}}\n\n'
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: str) -> dict:
    sess = SESSIONS.get(session_id)
    if sess is None:
        raise HTTPException(404, "unknown session")
    sess.cancel()
    return {"session_id": session_id, "cancelled": True}


@app.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    sess = SESSIONS.get(session_id)
    if sess is None:
        raise HTTPException(404, "unknown session")
    return {
        "session_id": session_id,
        "done": sess.done.is_set(),
        "cancelled": sess.cancelled,
        "event_count": len(sess.events),
    }
