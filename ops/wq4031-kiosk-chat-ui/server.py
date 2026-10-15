"""wq#4031: minimal FastAPI server for the Lumen chat kiosk web UI (RK3399 target).

Routes:
  GET  /                    kiosk UI (static/index.html)
  POST /api/chat            text in -> LLM reply out, via the LiteLLM gateway
  POST /api/camera/snapshot save a JPEG snapshot POSTed from the kiosk camera
  GET  /api/tts             text -> WAV bytes (Kokoro), played client-side via Web Audio API
  WS   /ws/voice            binary PCM16LE 16kHz mono in -> {"type": ..., "text": ...} /
                            binary WAV out (STT -> chat -> TTS), per wq#98's voice PoC

STT/TTS backends (faster-whisper, kokoro-onnx) are the same ones wq#98's PoC proved out
(reports/wq98_voice_poc_2026-07-10.md) and live in a separate "voice-venv" with torch/onnx
deps this server does not require to start. They're imported lazily so a kiosk box without
that venv still serves text chat and camera snapshot; voice/tts routes 503 until available.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from vad import FrameBuffer, VadStateMachine

LITELLM_BASE_URL = os.environ.get("FOREMAN_LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_KEY_FILE = os.environ.get("FOREMAN_LITELLM_KEY_FILE", "/home/lumen/.secrets/foreman-litellm.token")
KIOSK_MODEL = os.environ.get("LUMEN_KIOSK_MODEL", "messenger-chat")  # wq#98 report line 4
SNAPSHOT_DIR = Path(os.environ.get("LUMEN_KIOSK_SNAPSHOT_DIR", "/tmp/lumen-kiosk-snapshots"))

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="lumen-kiosk-chat")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def read_litellm_key() -> str:
    with open(LITELLM_KEY_FILE) as f:
        return f.read().strip()


async def chat_completion(message: str) -> str:
    headers = {"Authorization": f"Bearer {read_litellm_key()}"}
    payload = {
        "model": KIOSK_MODEL,
        "messages": [{"role": "user", "content": message}],
    }
    async with httpx.AsyncClient(base_url=LITELLM_BASE_URL, timeout=30.0) as client:
        resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


def _load_stt():
    from faster_whisper import WhisperModel  # voice-venv only
    return WhisperModel("base", device="cpu", compute_type="int8")


def _load_tts():
    from kokoro_onnx import Kokoro  # voice-venv only
    return Kokoro(
        os.environ.get("LUMEN_KOKORO_MODEL", "/home/lumen/models/kokoro/kokoro-v1.0.onnx"),
        os.environ.get("LUMEN_KOKORO_VOICES", "/home/lumen/models/kokoro/voices-v1.0.bin"),
    )


_stt_model = None
_tts_model = None


def get_stt():
    global _stt_model
    if _stt_model is None:
        _stt_model = _load_stt()
    return _stt_model


def get_tts():
    global _tts_model
    if _tts_model is None:
        _tts_model = _load_tts()
    return _tts_model


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/api/chat")
async def api_chat(request: Request) -> JSONResponse:
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    reply = await chat_completion(message)
    return JSONResponse({"reply": reply})


@app.post("/api/camera/snapshot")
async def api_camera_snapshot(file: UploadFile) -> JSONResponse:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"snapshot_{int(time.time() * 1000)}.jpg"
    dest = SNAPSHOT_DIR / name
    data = await file.read()
    dest.write_bytes(data)
    return JSONResponse({"ok": True, "path": str(dest)})


@app.get("/api/tts")
async def api_tts(text: str) -> Response:
    try:
        tts = get_tts()
    except ImportError:
        raise HTTPException(status_code=503, detail="tts backend not installed (voice-venv)")
    wav_bytes = tts.create_wav(text)
    return Response(content=wav_bytes, media_type="audio/wav")


@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    await ws.accept()
    frame_buffer = FrameBuffer()
    vad = VadStateMachine()
    speech_audio = bytearray()
    try:
        stt = get_stt()
    except ImportError:
        await ws.send_json({"type": "error", "detail": "stt backend not installed (voice-venv)"})
        await ws.close()
        return

    try:
        from silero_vad_score import score_window  # voice-venv only, injected scorer
    except ImportError:
        score_window = None

    try:
        while True:
            chunk = await ws.receive_bytes()
            for window in frame_buffer.push(chunk):
                confidence = score_window(window) if score_window else 1.0
                if vad.in_speech or confidence >= vad.speech_threshold:
                    speech_audio.extend(window)
                event = vad.feed(confidence)
                if event == "speech_end":
                    transcript = transcribe(stt, bytes(speech_audio))
                    speech_audio.clear()
                    await ws.send_json({"type": "transcript", "text": transcript})
                    if transcript.strip():
                        reply = await chat_completion(transcript)
                        await ws.send_json({"type": "reply", "text": reply})
                        try:
                            wav_bytes = get_tts().create_wav(reply)
                            await ws.send_bytes(wav_bytes)
                        except ImportError:
                            pass
    except WebSocketDisconnect:
        return


def transcribe(stt, pcm16_bytes: bytes) -> str:
    import numpy as np

    audio = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = stt.transcribe(audio, language="en")
    return " ".join(segment.text.strip() for segment in segments).strip()
