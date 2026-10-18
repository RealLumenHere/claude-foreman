# wq#4163 — verify-pass on wq#4160 (EVALUATE: WhisperX local STT for Lumen chat kiosk)

PR: lumen/claude-foreman#2107, merged d3f57b9f41 (commit dd593a9d in this
worktree). Author: claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh
instance, wq#4163).

## Claim under test

wq#4160's deliverable is a research doc, no code change:
`research/wq4160_stt_arm_viability_whisperx_vs_fasterwhisper_vs_whispercpp.md`.
Verdict claimed: keep `faster-whisper` (already wired in
`ops/wq4031-kiosk-chat-ui/server.py:59-60`) for the RK3399 kiosk; WhisperX
adds nothing for a single-speaker, turn-based, CPU-only kiosk because its
value (word alignment, diarization, GPU-batch throughput) answers problems
this kiosk doesn't have; whisper.cpp is a viable fallback only, not needed
now.

## What I checked

1. **Read the diff cold.** `git show dd593a9d` — single new markdown file,
   80 lines, no code touched. No probe script shipped (research ticket,
   archetype standard-engineering, no NO-PROBE marker either — treated
   "run its probe" as fact-checking every citation and line reference).
2. **Line-reference citations, checked against the actual files:**
   - `server.py:59-60` → read `ops/wq4031-kiosk-chat-ui/server.py` directly:
     lines 59-60 are exactly `def _load_stt(): from faster_whisper import
     WhisperModel ... return WhisperModel("base", device="cpu",
     compute_type="int8")`. Exact match, not paraphrased.
   - `vad.py`'s two "ported bugfixes" claim → read `vad.py`: `FrameBuffer`
     (512-sample rebuffering) and `VadStateMachine`'s silence-run counter
     that doesn't zero on the first silence window are both present and
     match the description.
   - wq#98 PoC numbers (`0.67s ASR latency`, x86 T5810, torchaudio
     CUDA/CPU wheel mismatch) → read
     `/home/lumen/agents/work-queue/reports/wq98_voice_poc_2026-07-10.md`
     directly: `asr_ms=658` / "end-of-utterance -> transcript_final: 0.67s
     (... faster-whisper base INT8 CPU)" on T5810, and "torchaudio 2.11.0
     CUDA wheel was paired with torch 2.12.1+cpu" — all three citations
     check out verbatim, no rounding or spin beyond "0.67s" for 0.658s.
3. **Tried to break the external citations (fabrication check, since
   these are the load-bearing premises for "WhisperX's speedup doesn't
   apply here").** Fetched the two most consequential ones directly
   (`curl`, no WebFetch available headless — see `kb/ISSUE-LOG.md` row
   1308):
   - `https://api.github.com/repos/SYSTRAN/faster-whisper/issues/911` —
     real, open comment quotes CTranslate2's README verbatim: "supports
     x86-64 and AArch64/ARM64 processors." Matches the doc's claim exactly.
   - `https://raw.githubusercontent.com/m-bain/whisperX/main/README.md` —
     real, contains "70x realtime with large-v2," "Batched inference for
     70x realtime transcription using whisper large-v2," wav2vec2
     alignment, and pyannote-audio diarization — all match the doc's
     characterization of WhisperX as a faster-whisper wrapper adding
     alignment + diarization, GPU-batch-dependent speedup.
   - Both real, both accurately represented. No fabricated URL/value
     (instinct 8 check: nothing here was invented and then used as its
     own evidence).
4. **Looked for a counter-argument the doc might have missed.**
   WhisperX's batching operates over VAD-segmented chunks *within* one
   audio file, not only across separate speakers/streams — so in
   principle a single long utterance with multiple VAD segments could
   still see a GPU-batch win. Doesn't change the verdict here: the kiosk
   is turn-based with short single utterances (one voice-button press per
   turn per README.md), so there's essentially one VAD segment per turn,
   and there's no GPU on the RK3399 regardless (Mali-T860, no mainline NN
   stack) — the batching speedup is CUDA-SM-dependent per WhisperX's own
   README, and CPU-only batching gives none of the claimed 70x. Noted as a
   precision nit, not a contradiction.
5. **Ops-bundle verify-contract gap** (ticket explicitly named this: `Bundle(s)
   without a --verify handler: wq4031-kiosk-chat-ui`). Confirmed no
   `install.sh` existed in `ops/wq4031-kiosk-chat-ui/` at all. Checked why:
   `wq show 4030` ("RK3399 Face X8: install Webview Kiosk APK and deploy
   chat UI", lane `frank-hw`, status `open`) detail reads "Requires: Lumen
   chat web server deployed first" — the deploy host/unit for this server
   is genuinely undecided, confirmed by `systemctl list-unit-files | grep
   -i kiosk` on Lumen turning up only the unrelated `lumen-kiosk-idle.service`
   (Pokemon LPP cover-serving, per `ops/wq2751-listener-audit/README.md:20`).
   Added `ops/wq4031-kiosk-chat-ui/install.sh` with a read-only `--verify`
   handler per `ops/VERIFY_CONTRACT.md`'s "a bundle that installs nothing at
   all still needs a handler" rule: it checks for any `kiosk-chat*` unit on
   Lumen and reports one `MISS` line if absent (it is absent — honest
   negative, not a repo-only self-compare, which the ticket explicitly says
   does not count). Body falls through to a non-destructive `exit 1` with a
   message pointing at the undecided deploy target; no install step defined
   because the design doc (`/home/lumen/scratch/research/rk3399-chat-face-design.md`)
   is outside this worktree's granted dirs and the host/unit naming isn't
   decided anywhere I can read.
   - Ran it for real: `VERIFY FAIL (1)` / `MISS not deployed: no
     kiosk-chat-ui systemd unit on Lumen ... -- wq#4030, frank-hw, open,
     needs the chat web server deployed first`, exit 1.
   - Confirmed the probe can fail AND pass (wq#2630): stubbed `systemctl`
     on `PATH` to report a fake `lumen-kiosk-chat-ui.service` present →
     `VERIFY OK`, exit 0. Confirms the check isn't vacuous.
   - `python3 ops/deploy_triage.py --check` still reports `OK 36 bundles, 0
     problem(s)` (that script's register is the fixed wq#2985 backfill
     list; this new bundle isn't in it, which is expected — `ops-deploy-drift`
     picks up new handlers from its own `ROOTS` scan, `/home/lumen/foreman`
     is in that list).
   - `python3 -m pytest ops/wq4031-kiosk-chat-ui/tests/` — 10 passed,
     unaffected by the new `install.sh`.

## Verdict

**CONFIRM.** Every file:line citation and every external claim the doc
rests its recommendation on checked out exactly against the real files and
the real GitHub content — nothing fabricated, nothing misquoted. The
one gap in scope (WhisperX's batching technically can apply within a
single long utterance) doesn't move the recommendation for this kiosk's
actual turn-based, short-utterance, no-GPU shape. "No code change" is the
right call given `faster-whisper` is already wired exactly as described.
The required ops-bundle `--verify` handler for `wq4031-kiosk-chat-ui` is
now in place and correctly reports the honest, already-tracked gap (not
live anywhere yet, blocked on the open `frank-hw` chain wq#4029/wq#4030) —
this does not contradict wq#4160, which itself flagged "untested on the
actual RK3399 unit as of this writing" as an open item, not a blocker to
its recommendation.

No escalation.
