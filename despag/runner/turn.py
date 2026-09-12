"""wq#1806: one `claude -p` subprocess per turn (PHASE0_WIRING_SPEC.md RECOMMENDATION
step 2, C.2). `Turn` owns exactly one child process: spawn, read its canonical
`stream-json` frames off a background thread, enforce the wall-clock cap and the
frame-gap (inactivity) watchdog, and kill the whole process GROUP — never just the
CLI pid — on cancel, cap, or hang. `TurnRegistry` is the in-memory table the gateway
polls by turn id, with the same finished-record eviction HR needed once it stopped
being a one-process-per-attempt supervisor (E.7).
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
import time

from despag.runner import canonical


def _envint(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# HR/runner/server.py:392 MAX_TURN_SECONDS — the hard abuse-backstop ceiling every
# turn's own timeout_seconds is capped by, regardless of what the caller asked for.
DESPAG_MAX_TURN_SECONDS_DEFAULT = 21600
# fworker.py:269 INACTIVITY_TIMEOUT_S is the same idea (no frame this long = stalled)
# at a fleet-worker default (240s) tuned for agentic coding turns; despag's default is
# wider because a single UHP task can legitimately sit quiet through one long tool call.
DESPAG_FRAME_GAP_SECONDS_DEFAULT = 900
# HR/runner/server.py:4096 _TURN_RETENTION_S — grace past the hard cap during which a
# finished record must still answer a poll that raced its own completion.
_TURN_RETENTION_S = 30 * 60


def _reader_thread(pipe, q: "queue.Queue") -> None:
    """Background thread: forward complete lines off the child's merged stdout/stderr
    pipe onto `q` so the watchdog loop can enforce time-based conditions without
    blocking on readline() (fworker.py:1156-1174 _reader_thread, same contract)."""
    try:
        for line in iter(pipe.readline, ""):
            q.put(("line", line))
    except (OSError, ValueError) as e:
        q.put(("reader_error", str(e)))
    finally:
        q.put(("eof", None))


class Turn:
    """One `claude -p` child process and its accumulated canonical frames.

    `status` is set exactly once, when `run()` returns, to one of: "completed",
    "failed", "incomplete", "cancelled", "timeout" (wall-clock cap — a budget stop,
    the UHP-facing translator maps this to `incomplete`), "hang" (frame-gap watchdog
    fired — no result ever arrived, so this is not a budget stop; the UHP-facing
    translator maps it to `failed`).
    """

    def __init__(self, turn_id: str, cmd: list[str], cwd: str, env: dict,
                 *, timeout_seconds: float | None = None, frame_gap_s: float | None = None,
                 on_frame=None):
        self.id = turn_id
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        # wq#1809: the live-SSE bridge. Called synchronously on THIS turn's own thread
        # right after a canonical frame is appended to `self.frames` — a caller that
        # wants the frame the instant it exists (not on the next poll) subscribes here
        # rather than diffing `snapshot_frames()`, which is what "flush the moment the
        # reader thread appends it, no polling" (streaming.md §1) requires.
        self.on_frame = on_frame
        max_turn_cap = _envint("DESPAG_MAX_TURN_SECONDS", DESPAG_MAX_TURN_SECONDS_DEFAULT)
        # C.2: cap = min(caller's timeout_seconds, the hard ceiling) — a caller can ask
        # for less than the ceiling, never more.
        self.wall_clock_s = min(timeout_seconds, max_turn_cap) if timeout_seconds else max_turn_cap
        self.frame_gap_s = (frame_gap_s if frame_gap_s is not None
                             else _envint("DESPAG_FRAME_GAP_SECONDS", DESPAG_FRAME_GAP_SECONDS_DEFAULT))

        self.frames: list[dict] = []
        self.status: str | None = None
        self.killed_reason: str | None = None
        self.exit_code: int | None = None
        self.error: str | None = None
        self.started_at: float | None = None
        self.done_at: float | None = None
        self.done = threading.Event()

        self.proc: subprocess.Popen | None = None
        self._frames_lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._state: dict = {}   # per-turn scrub state threaded through canonical.normalize

    # -- public API -----------------------------------------------------------------

    def cancel(self) -> None:
        """Request cancellation. Safe to call before spawn (race handled in `run()`),
        during the run (kills immediately), or after completion (no-op — a finished
        turn's recorded status must never change, C-02)."""
        self._cancel_requested.set()
        if self.proc is not None and not self.done.is_set():
            self._kill()

    def snapshot_frames(self) -> list[dict]:
        with self._frames_lock:
            return list(self.frames)

    # -- internals --------------------------------------------------------------------

    def _kill(self) -> None:
        """SIGKILL the child's whole process GROUP — start_new_session=True at spawn
        makes the child's pid its own pgid, so a group kill also reaches any Bash-tool
        grandchild (build, sleep, server) that would otherwise survive the CLI's own
        death and keep the inherited stdout pipe open (PHASE0_WIRING_SPEC.md E.1/C.2,
        HR/runner/server.py:4116-4127 `_kill_proc_tree` — SIGKILL directly, no SIGTERM
        grace: fworker's pid-only SIGTERM+30s+SIGKILL is exactly the shape that leaves
        those children holding the pipe)."""
        if self.proc is None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass

    def _release_proc(self) -> None:
        """Close every pipe the child held, in `finally`, before dropping the handle.
        A retained `Popen` leaks 2-3 fds per turn even after the process has exited;
        HR measured EMFILE at 1020/1024 fds after 506 turns with this omitted
        (PHASE0_WIRING_SPEC.md E.7, HR/runner/server.py:4071-4090 `_release_proc`)."""
        if self.proc is None:
            return
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self.proc = None

    def run(self) -> None:
        """Blocking. Spawn the child, read+normalize its frames until a terminal
        condition, then finalize `status`. Intended to run on a dedicated thread
        (see `TurnRegistry.start`) — one thread per in-flight turn, matching "one
        claude -p per turn"."""
        self.started_at = time.time()
        try:
            proc = subprocess.Popen(
                self.cmd, cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True,
            )
        except OSError as e:
            self.killed_reason = "spawn_error"
            self.status = "failed"
            self.error = f"spawn: {e}"[:500]
            self.done_at = time.time()
            self.done.set()
            return
        self.proc = proc
        if self._cancel_requested.is_set():
            # Cancel-before-spawn race: the caller called cancel() in the window
            # between Turn creation and this Popen returning. Checked immediately
            # after spawn, before any read — kill now rather than waiting for pipe EOF.
            self._kill()

        timer = threading.Timer(self.wall_clock_s, self._on_wall_clock_cap)
        timer.daemon = True
        timer.start()

        q: "queue.Queue" = queue.Queue()
        reader = threading.Thread(target=_reader_thread, args=(proc.stdout, q), daemon=True)
        reader.start()

        result_frame: dict | None = None
        last_event = time.time()
        poll_timeout = max(0.05, min(1.0, self.frame_gap_s / 4))
        try:
            while True:
                if self._cancel_requested.is_set():
                    self.killed_reason = "cancelled"
                    break
                now = time.time()
                if now - last_event >= self.frame_gap_s:
                    self.killed_reason = "inactivity"
                    break
                try:
                    kind, payload = q.get(timeout=poll_timeout)
                except queue.Empty:
                    continue
                if kind == "eof":
                    break
                if kind == "reader_error":
                    self.killed_reason = "reader_error"
                    self.error = str(payload)
                    break
                last_event = time.time()
                line = payload.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                for ev in canonical.normalize(frame, self._state):
                    with self._frames_lock:
                        self.frames.append(ev)
                    if ev.get("type") == "result":
                        result_frame = ev
                    if self.on_frame is not None:
                        self.on_frame(ev)
        finally:
            timer.cancel()
            if self.killed_reason is None and self._cancel_requested.is_set():
                # A cancel() racing the read loop can kill the child and make its
                # pipe hit EOF before the loop's own top-of-iteration check notices
                # the cancellation flag — the "eof" branch above breaks with no
                # killed_reason set. Recheck here so a cancelled turn is never
                # misreported as a plain (or exit-code-driven "failed") completion.
                self.killed_reason = "cancelled"
            if self.killed_reason is not None:
                self._kill()
            if self.proc is not None:
                self.exit_code = self.proc.wait()
            self._release_proc()

        if self.exit_code is not None and self.exit_code < 0:
            # POSIX: a signal-terminated child reports returncode -signum. Normalize
            # to the shell convention (128+signum) — fworker.py:1334-1340 does the
            # same so SIGKILL reads as 137 wherever it's inspected.
            self.exit_code = 128 - self.exit_code

        if self.killed_reason == "cancelled":
            self.status = "cancelled"
        elif self.killed_reason == "wall_clock":
            self.status = "timeout"
        elif self.killed_reason == "inactivity":
            self.status = "hang"
        elif self.killed_reason == "reader_error":
            self.status = "failed"
        else:
            self.status = canonical._status_from_result(result_frame, self.exit_code)
        self.done_at = time.time()
        self.done.set()

    def _on_wall_clock_cap(self) -> None:
        self.killed_reason = "wall_clock"
        self._kill()


class TurnRegistry:
    """The in-memory turn table the gateway polls by id. One process serves every
    concurrent turn (PHASE0_WIRING_SPEC.md RECOMMENDATION "single most important thing
    to prove"), so this is the seam that makes "≥3 concurrent Claude-on-Max sessions"
    a fact about this table, not about separate processes."""

    def __init__(self):
        self._turns: dict[str, Turn] = {}
        self._lock = threading.Lock()

    def start(self, turn_id: str, cmd: list[str], cwd: str, env: dict,
              *, timeout_seconds: float | None = None, frame_gap_s: float | None = None,
              on_frame=None) -> Turn:
        turn = Turn(turn_id, cmd, cwd, env, timeout_seconds=timeout_seconds, frame_gap_s=frame_gap_s,
                    on_frame=on_frame)
        with self._lock:
            self._turns[turn_id] = turn
        threading.Thread(target=turn.run, daemon=True, name=f"despag-turn-{turn_id}").start()
        return turn

    def get(self, turn_id: str) -> Turn | None:
        with self._lock:
            return self._turns.get(turn_id)

    def evict_finished(self, now: float | None = None) -> int:
        """Drop finished records older than MAX_TURN_SECONDS + 30min past their start
        — no live poll can legitimately still want one this old (HR/runner/server.py:
        4092-4111 `_evict_turns`, PHASE0_WIRING_SPEC.md C.2/E.7)."""
        now = now if now is not None else time.time()
        max_turn_cap = _envint("DESPAG_MAX_TURN_SECONDS", DESPAG_MAX_TURN_SECONDS_DEFAULT)
        retention_s = max_turn_cap + _TURN_RETENTION_S
        gone = 0
        with self._lock:
            for tid, turn in list(self._turns.items()):
                if turn.done.is_set() and turn.started_at is not None and now - turn.started_at > retention_s:
                    del self._turns[tid]
                    gone += 1
        return gone
