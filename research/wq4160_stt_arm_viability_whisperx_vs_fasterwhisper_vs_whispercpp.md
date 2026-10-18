# wq#4160 — STT engine choice for the RK3399 chat kiosk: WhisperX vs faster-whisper vs whisper.cpp

## Verdict
**Keep faster-whisper** (already the engine wired into the kiosk at
`ops/wq4031-kiosk-chat-ui/server.py:59-60`, `WhisperModel("base", device="cpu",
compute_type="int8")`). It is CPU-only viable, already proven end-to-end on Lumen fleet
hardware (wq#98 PoC, x86 T5810), and is the right fit for the RK3399's weak CPU and small
RAM budget. WhisperX is not a different ASR engine to compare against on throughput terms —
it is a wrapper *around* faster-whisper that adds word-level alignment (wav2vec2) and
diarization (pyannote), both torch-heavy and both irrelevant to a single-speaker,
turn-based kiosk. whisper.cpp is a viable fallback if the voice-venv's CTranslate2/torch
dependency stack ever becomes a packaging problem on the kiosk image, but it buys no speed
or accuracy advantage over faster-whisper on this SoC and loses the batching/VAD
integration already built in `ops/wq4031-kiosk-chat-ui/vad.py`.

## Target hardware
RK3399: 2x Cortex-A72 @ ~1.8GHz + 4x Cortex-A53 @ ~1.4GHz, no usable GPU for ML inference
(Mali-T860 has no mainline NN/compute stack Whisper's ecosystem targets) — treat as
CPU-only, roughly comparable to a low-end Raspberry Pi 3/4-class quad+dual big.LITTLE part,
weaker than the Cortex-A76 Raspberry Pi 5 benchmarks cited below.

## Prior art (fleet-search, 2026-10-18)
- CTranslate2 (faster-whisper's inference backend) ships prebuilt wheels and "supports
  x86-64 and AArch64/ARM64 processors" directly
  (https://github.com/SYSTRAN/faster-whisper/issues/911) — confirms faster-whisper is not
  an x86-only port.
- whisper.cpp's ggml backend is ARM-NEON-optimized by design; community reports show it
  doing real-time streaming transcription on a Raspberry Pi 5
  (https://www.reddit.com/r/raspberry_pi/comments/1c8w3a6/) and a direct 2026 whisper.cpp
  vs faster-whisper CPU shootout notes "All inference is CPU (ARM NEON) — the Pi has no
  usable GPU path for Whisper" (https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison).
- Raspberry Pi 5 CPU realtime factors reported for faster-whisper: ~0.5x–1.5x depending on
  model size (https://localaimaster.com/blog/faster-whisper-guide) — i.e. even on
  Pi5-class hardware the "small"/"medium" models are already at or below real-time; the
  RK3399's older/slower cores push the usable model size further down toward
  "tiny"/"base", which is exactly what wq#98 already benchmarked (base INT8 CPU, 0.67s
  ASR latency, see `/home/lumen/agents/work-queue/reports/wq98_voice_poc_2026-07-10.md:9`).
- WhisperX's own README advertises "batched inference for 70x realtime ... using whisper
  large-v2" (https://github.com/m-bain/whisperx) — the headline speedup is a GPU batching
  trick (large batches saturate a CUDA SM); it does not apply on a single-stream,
  single-speaker, CPU-only kiosk request, where there is no batch to form.

## Comparison

| | faster-whisper | WhisperX | whisper.cpp |
|---|---|---|---|
| ASR backend | CTranslate2 (own engine) | wraps faster-whisper | ggml, from-scratch C/C++ |
| ARM/CPU support | Yes — official AArch64 wheels | Yes, inherits faster-whisper's, PLUS torch (wav2vec2 align) + pyannote (diarization), both CPU-capable but heavier installs | Yes — ARM NEON is a first-class ggml target |
| Extra deps beyond ASR | none required | torch, torchaudio, pyannote.audio, alignment models per language | none (single static-ish binary + model file) |
| Useful extras for this kiosk | — | word timestamps + diarization: **not needed** — single speaker, turn-based voice button, no transcript UI that needs word-level highlighting | — |
| Already integrated | Yes: `ops/wq4031-kiosk-chat-ui/server.py:59-60`, VAD in `ops/wq4031-kiosk-chat-ui/vad.py` | No | No |
| Fleet-proven latency | 0.67s ASR for "base" INT8 CPU, x86 (wq#98) | not measured; adds alignment-model load + inference on top | not measured on fleet hardware |
| Python ecosystem fit | Same venv already used for Kokoro TTS leg | New heavy deps (torch stack) duplicate what messenger's voice-venv already fought through (torchaudio/CUDA-vs-CPU wheel mismatch, wq#98 finding) | Would need a separate binary + ctypes/subprocess bridge into the FastAPI server, or a Python binding (pywhispercpp) |

## Why not WhisperX here
WhisperX's value proposition (word-level alignment timestamps, speaker diarization,
large-batch GPU throughput) answers problems the kiosk doesn't have: one speaker, one
utterance per turn, no multi-speaker transcript, no GPU. Pulling it in means carrying
torch + pyannote + alignment-model downloads on an ARM image for zero behavioral benefit,
and re-fighting the exact torch/torchaudio CPU-vs-CUDA wheel mismatch wq#98 already
root-caused and fixed for the voice-venv (see wq98 report, "Changes" item 2: torchaudio
2.11.0 CUDA wheel paired with CPU torch).

## Why not whisper.cpp (for now)
whisper.cpp is the better pure-throughput choice on ARM NEON in principle, and is a
reasonable fallback if the voice-venv's torch/CTranslate2 footprint ever becomes a problem
on the RK3399 image (smaller binary, no Python ML stack to cross-build for aarch64). But
switching now would mean re-deriving the VAD/session-lifecycle integration already working
in `ops/wq4031-kiosk-chat-ui/vad.py` and `server.py`, for a model (`base`, INT8) that
faster-whisper already runs at sub-second latency on CPU. No measured need justifies the
rewrite; revisit only if RK3399 bring-up shows faster-whisper's CTranslate2 wheel is
unavailable/slow for aarch64 in practice (untested on the actual RK3399 unit as of this
writing — flagging as the one open verification item, not a blocker to the recommendation).

## Recommendation
No code change: `ops/wq4031-kiosk-chat-ui/server.py`'s existing `faster-whisper` CPU/INT8
choice is confirmed as the right one for the RK3399 kiosk. Open follow-up (separate
ticket, physical-hardware-gated): run the wq#98 PoC's ASR-only leg on an actual RK3399 unit
to confirm the 0.67s "base" latency scales acceptably on the weaker CPU — Lumen doesn't
have this measured on RK3399 silicon yet, only on the x86 T5810.
