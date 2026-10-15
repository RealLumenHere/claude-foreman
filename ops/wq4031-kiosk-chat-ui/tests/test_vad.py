import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vad import WINDOW_BYTES, WINDOW_SAMPLES, FrameBuffer, VadStateMachine


def test_frame_buffer_rebuffers_arbitrary_chunks_into_fixed_windows():
    buf = FrameBuffer()
    # 480-sample chunks (common network read size), not the required 512-sample window
    chunk = b"\x00\x01" * 480
    windows = buf.push(chunk)
    assert windows == []  # not enough for one window yet
    windows = buf.push(chunk)
    assert len(windows) == 1
    assert len(windows[0]) == WINDOW_BYTES
    assert len(windows[0]) // 2 == WINDOW_SAMPLES


def test_frame_buffer_preserves_byte_order_across_pushes():
    buf = FrameBuffer()
    data = bytes(range(256)) * 10  # 2560 bytes = 2 windows + remainder
    windows = buf.push(data)
    assert b"".join(windows) == data[: len(windows) * WINDOW_BYTES]


def test_speech_end_fires_after_silence_run_not_on_first_silence_window():
    # wq#98 bugfix under test: speech counter must not zero on the FIRST silence
    # window, or speech_end can never fire (reports/wq98_voice_poc_2026-07-10.md:27-28).
    vad = VadStateMachine(speech_threshold=0.5, silence_run_needed=3)
    assert vad.feed(0.9) == "speech_start"
    assert vad.feed(0.9) is None
    assert vad.feed(0.1) is None  # first silence window: must NOT end speech yet
    assert vad.in_speech is True
    assert vad.feed(0.1) is None
    assert vad.feed(0.1) == "speech_end"
    assert vad.in_speech is False


def test_speech_resumes_silence_run_counter_on_renewed_speech():
    vad = VadStateMachine(speech_threshold=0.5, silence_run_needed=2)
    vad.feed(0.9)
    vad.feed(0.1)  # 1 silence window
    vad.feed(0.9)  # speech resumes -> silence run must reset
    assert vad.feed(0.1) is None  # 1 silence window again, not 2
    assert vad.feed(0.1) == "speech_end"


def test_no_speech_end_without_prior_speech_start():
    vad = VadStateMachine(silence_run_needed=1)
    assert vad.feed(0.0) is None
    assert vad.feed(0.0) is None
