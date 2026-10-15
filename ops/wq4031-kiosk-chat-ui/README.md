# wq#4031 — Lumen chat kiosk web UI

Minimal FastAPI server + static web UI for the RK3399 kiosk: text chat, voice button
(WebSocket audio -> Whisper STT), camera snapshot, and Kokoro TTS playback via the Web
Audio API. Targets both Android 7.1.2 WebView and modern Chromium.

## Layout
- `server.py` — FastAPI app: `/`, `/api/chat`, `/api/camera/snapshot`, `/api/tts`, `/ws/voice`.
- `vad.py` — frame rebuffering + speech-start/speech-end state machine (no torch dep; the
  Silero scorer is injected so this is unit-testable without the voice-venv).
- `static/index.html`, `static/app.js`, `static/style.css` — kiosk UI. Plain `<script>`,
  no ES modules, no optional chaining, feature-detected `getUserMedia`/`AudioContext` —
  safe on the Android 7.1.2 WebView as well as a modern Chromium tab. Audio capture uses
  `ScriptProcessorNode` (not `AudioWorklet`, which needs ES-module support the older
  WebView doesn't reliably have) to turn mic input into 16kHz PCM16 frames sent over the
  `/ws/voice` WebSocket.
- `tests/` — `vad.py` state-machine tests + `server.py` route tests (`TestClient`, LLM
  and TTS backends monkeypatched).

## Reuse of wq#98 (voice PoC)
Per `reports/wq98_voice_poc_2026-07-10.md` (mirrored at
`/home/lumen/agents/work-queue/reports/wq98_voice_poc_2026-07-10.md`; the full-duplex
server itself, `/home/lumen/messenger/live_voice_server.py`, is outside this worktree's
scope):
- Same STT/TTS pair: faster-whisper (`base`, INT8, CPU) and Kokoro-82M ONNX
  (`/home/lumen/models/kokoro/{kokoro-v1.0.onnx,voices-v1.0.bin}`, overridable via
  `LUMEN_KOKORO_MODEL`/`LUMEN_KOKORO_VOICES`).
- Same two VAD bugfixes ported into `vad.py`: Silero's fixed 512-sample/16kHz window
  requirement (`FrameBuffer`), and the silence-run counter that must not zero on the
  first silence window or `speech_end` can never fire (`VadStateMachine`).
- Same LLM leg: LiteLLM gateway (`FOREMAN_LITELLM_BASE_URL`, default
  `http://localhost:4000`) to the `messenger-chat` model group (override via
  `LUMEN_KIOSK_MODEL`), matching `despag/backends/litellm.py`'s env-var convention and
  key-file-read-per-call discipline (never cached in process env).

## Backends are optional at import time
`faster_whisper` and `kokoro_onnx` live in a separate "voice-venv" (torch/onnx deps) per
the PoC report, not this server's base env. `server.py` imports them lazily inside
`_load_stt`/`_load_tts`; `/api/tts` and `/ws/voice` return a 503 / `{"type": "error"}`
instead of crashing the server when that venv isn't the one running it. Text chat and
camera snapshot work with no ML deps installed at all.

## Running
```
cd ops/wq4031-kiosk-chat-ui
uvicorn server:app --host 0.0.0.0 --port 8080
```
For voice/TTS, run under the voice-venv described in the PoC report (adds
`faster-whisper`, `kokoro-onnx`, `torchaudio==2.11.0+cpu`).

## Tests
```
python3 -m pytest ops/wq4031-kiosk-chat-ui/tests/
```
