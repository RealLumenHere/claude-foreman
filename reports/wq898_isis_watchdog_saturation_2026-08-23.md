# wq#898 — Isis ollama single-slot saturation false "rung down" alerts

## Root cause

Isis (`100.100.129.24:11434`) serves `qwen3.6:35b-a3b` (qwen35moe arch) with exactly one decode
slot. Confirmed via `ollama ps` (`PROCESSOR 100% CPU`, no GPU) and the running `llama-server`
command line: `-np 1`. Journal at boot (`journalctl -u ollama`, 2026-08-23 12:16:47Z):

    level=WARN source=sched.go:509 msg="model architecture does not currently support parallel
    requests" architecture=qwen35moe

This fires despite `OLLAMA_NUM_PARALLEL=4` being set in the systemd drop-in
(`/etc/systemd/system/ollama.service.d/override.conf`) — the qwen35moe architecture hard-blocks
parallel slots in the installed ollama build (0.32.6), so the env var is a no-op for this model.
**Raising `OLLAMA_NUM_PARALLEL` was evaluated and ruled out**: it's not a RAM/ctx tradeoff, it's an
architectural block regardless of the box's 60G RAM.

With one slot, a real production decode (LPP brain `qwen36-local` + local-first mechanical-edit
rung via LiteLLM, 686 `/api/generate` calls in 10h, many 3-4min at ~10 tok/s) fully occupies the
slot for its duration. `model-watchdog`'s own probe queues behind it on the same
`/chat/completions` -> `/api/generate` path and times out (`LOCAL_TIMEOUT=90s`), and the watchdog
treated that timeout as DOWN — 9h of false "rung down" alerts (06:28-15:02Z) while Isis was
actually serving traffic the whole time (confirmed live via `print_timing` lines, not a hang).

**Dedicated tiny-probe model was also evaluated and ruled out**: the box runs at ~56/60G RSS with
`OLLAMA_MAX_LOADED_MODELS=1`; loading a second model concurrently risks eviction thrash of the
production model for marginal benefit, and doesn't test the thing operators actually care about
(is the *production* rung responsive).

## Fix applied

`/home/lumen/agents/model-watchdog/lumen_model_watchdog.py` (live infra script, not in this repo;
backed up to `lumen_model_watchdog.py.bak.20260823_152600_pre_wq898_busy_probe` before edit, per
existing convention in that directory).

Added a queue-depth-aware liveness check for LOCAL models: before declaring a failed generate
probe DOWN, hit the model's native ollama `/api/tags` directly (bypasses the busy decode slot
entirely — answers in <1ms even under full saturation, per the audit). If `/api/tags` is up but
the generate probe timed out, that's BUSY not DOWN: carry forward the previous up/down state
instead of alerting, and log it distinctly. If `/api/tags` is also unreachable, it's a real DOWN
and alerts fire as before. No timeout values changed; no Isis-side config touched. Verified:
`ollama_alive()` returns `True` against Isis and `False` against a dead port; a live watchdog run
during today's saturation window still reported `qwen36-local` up correctly. Next scheduled run
(`lumen-model-watchdog.timer`, every 10min, oneshot) picks up the change with no restart needed.

## Also found: malformed `/api/generate/api/show` (404)

Isis journal shows sporadic (~5x/10h) `POST /api/generate/api/show` returning 404, source IP
`100.117.66.102` = the `lumen-1` tailnet peer, i.e. the Lumen host itself (Docker NATs the
containerized LiteLLM's egress through the host's tailscale IP).

Ruled out as the caller, by direct inspection: every script in this codebase and on the Lumen host
that talks to Ollama directly (`lumen_agent.py`, `minerva/memory_audit.py`, `bench_ollama.py`,
`quality_check.py`, `minerva/model_audit.py`, `minerva/vision-eval/run_eval.py`,
`messenger/rage_library_generator.py`, `brain_healthcheck.py`) builds its URL cleanly — no
double-appended path anywhere. `qwen36-local` is the only Isis-pointed model with real traffic
(the two other Isis aliases in `litellm_config.yaml`, `isis-qwen3-32b` / `isis-qwen36`, are
otherwise-unused and never surfaced the bug either, consistent with it needing live traffic to
trigger).

The URL shape (`{completion_url}/api/show` instead of `{api_base}/api/show`) is the signature of
LiteLLM's own `ollama/` (non-chat) provider internally re-deriving its template-lookup URL from
the already-built `/api/generate` completion URL instead of the original `api_base`. LiteLLM is
vendored (`ghcr.io/berriai/litellm@sha256:6c82...`, no local source to patch) and no model in this
fleet's `litellm_config.yaml` has ever used the `ollama_chat/` provider prefix, which talks to
Ollama's native `/api/chat` and doesn't need this template fetch at all — that's the standing fix,
but it would be the first-ever use of that provider for this fleet and changes how prompts reach
the live LPP brain rung. **Not applied**: cosmetic-only impact (404, no effect on the 686
successful production calls), and switching provider type on a currently-loaded production brain
model without a way to verify safe LiteLLM hot-reload behavior is out of scope for an unattended
build session. Flagging for a maintenance-window follow-up: swap `qwen36-local`'s
`model: ollama/qwen3.6:35b-a3b` to `model: ollama_chat/qwen3.6:35b-a3b` in
`/home/lumen/agent/litellm_config.yaml`, reload, and confirm both a clean Isis journal and
unchanged brain output quality before calling it done.
