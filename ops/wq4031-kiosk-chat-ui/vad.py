"""wq#4031: frame buffering + speech-end state machine for the kiosk voice WS.

Reuses the two bugfixes wq#98's PoC report (reports/wq98_voice_poc_2026-07-10.md,
lines 27-31, mirrored at /home/lumen/agents/work-queue/reports/wq98_voice_poc_2026-07-10.md)
logged against /home/lumen/messenger/live_voice_server.py:

  1. The speech-run counter must NOT reset on the first silence window — it needs a run
     of consecutive silence windows before firing speech_end, or speech_end can
     mathematically never fire once any single silence window follows speech.
  2. Silero VAD requires exactly 512-sample windows at 16kHz; the network delivers
     arbitrary-sized PCM16 chunks, so they must be rebuffered into fixed windows before
     scoring.

The actual Silero scorer lives in the voice-venv (torch dependency) and is injected as
`score_fn` rather than imported here, so this module -- and its tests -- have no torch
dependency.
"""
from __future__ import annotations

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512  # Silero v5 required window size at 16kHz
WINDOW_BYTES = WINDOW_SAMPLES * 2  # PCM16 mono


class FrameBuffer:
    """Rebuffers arbitrary-sized PCM16LE chunks into fixed 512-sample windows."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def push(self, chunk: bytes) -> list[bytes]:
        self._buf.extend(chunk)
        windows = []
        while len(self._buf) >= WINDOW_BYTES:
            windows.append(bytes(self._buf[:WINDOW_BYTES]))
            del self._buf[:WINDOW_BYTES]
        return windows


class VadStateMachine:
    """Speech-start/speech-end detector over a stream of per-window VAD confidences.

    `speech_threshold` / `silence_run_needed` default to the values the PoC report
    measured as working on faster-whisper + Silero v5 (base INT8, 16kHz mono).
    """

    def __init__(self, speech_threshold: float = 0.5, silence_run_needed: int = 20) -> None:
        self.speech_threshold = speech_threshold
        self.silence_run_needed = silence_run_needed
        self._in_speech = False
        self._silence_run = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, confidence: float) -> str | None:
        """-> "speech_start", "speech_end", or None for this window."""
        is_speech = confidence >= self.speech_threshold
        if is_speech:
            self._silence_run = 0
            if not self._in_speech:
                self._in_speech = True
                return "speech_start"
            return None
        if not self._in_speech:
            return None
        # bugfix (wq#98): count consecutive silence windows, don't zero on the first one
        self._silence_run += 1
        if self._silence_run >= self.silence_run_needed:
            self._in_speech = False
            self._silence_run = 0
            return "speech_end"
        return None
