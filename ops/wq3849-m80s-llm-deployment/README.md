# wq#3849 — M80s LLM deployment: install Ollama + models when hardware arrives

## Status check performed before writing anything here

The ticket says "when hardware arrives." Verified live rather than assumed (doctrine:
never guess a path/address/status):

- `lumen_state.py current m80s` — no entry. `fleet-exec-policy.yaml` — zero `m80` hits.
- `research/fleet_hardware.json` node `m80s`: `"status": "unprovisioned-physical"`.
- `wq show 3703` (MEBx/vPro AMT config + Ubuntu install on nvme0n1) — **status: blocked**,
  genuinely physical/console-only (no remote KVM exists before AMT is configured).
- `wq show 3705` (fworker user/NVMe/unit install, `~/.claude/` migration, LiteLLM key
  mint) — **status: open**, blocked on wq#3703, needs a privileged root session once the
  box has an OS.
- `wq show 3704` (MeshCommander on Thor) — done, but only a runbook; THOR_HARDLOCK means
  no fleet session can start that container either.

So the M80 is not network-reachable: no SSH target, no hostname, no IP. Nothing in this
ticket that requires touching the box can execute from this or any headless session
today. Per full-autonomy instinct 3, that is a physical/privileged dependency to queue,
not a gate that blocks the rest of the work — and the queue already exists (wq#3703 →
wq#3705) so this ticket does not file a duplicate.

Separately: `fleet-config/docs/RECOVERY.md` §4 — the live LiteLLM config
(`/home/lumen/agent/litellm_config.yaml`) is credential-bearing, 0600, and never
committed to this repo. And `research/fleet_hardware.json`'s `network_note` plus
`kb/ISSUE-LOG.md` row for wq#1986 confirm the fworker guard denies `ollama pull` from
worker context outright (wq#1297, bridge-risk, manual/off-peak only). So even once the
node is reachable, the model pulls and the live config edit are privileged-session
actions, not something a BUILD worker runs directly — same split wq#1986's
`apply.sh`/`pull-*` subcommands already use for the isis/mistral routes.

## What this PR delivers (the authorable half)

Everything below is ready to run unmodified the moment M80s has a hostname/IP in
`fleet-exec-policy.yaml` and `lumen_state.py`:

| File | Purpose | Who runs it |
|---|---|---|
| `install-ollama.sh` | Idempotent Ollama install + P-core pin + env tuning | privileged session on M80s |
| `ollama-pcore-pin.conf` | systemd drop-in: `CPUAffinity=0-15`, `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1` | installed by `install-ollama.sh` |
| `pull-models.sh` | Pulls the 4 ticket models by tag | privileged session, manual/off-peak (wq#1297) |
| `benchmark.py` | Benchmarks each pulled model at `num_ctx=16384` and `32768` via `/api/generate`, writes `benchmark-results.json` | privileged session or a read-only fleet-exec grant once M80s is onboarded |
| `litellm-aliases.yaml` | `model_list` fragment for `agent-fast`/`agent-deep`/`research`/`quick` | input to `apply.sh` |
| `apply.sh` | Merges the fragment into the live `litellm_config.yaml`, backs it up, restarts the container, verifies `/health/liveliness` — same shape as `ops/wq1986-quantize-local-models/apply.sh` | privileged session on Lumen, after M80s's real `api_base` is filled in |
| `COMPARISON.md` | T5810 (Lumen) baseline, cited from `research/LOCAL_MODEL_STRATEGY.md`, and the M80s planning estimates from `research/WQ3798_M80S_LLM_MODEL_SELECTION.md` / `scratch/research/m80s-llm-recommendations.md`, with an empty results table for `benchmark.py`'s output | reference doc |

## Model tags (as specified by the ticket)

- `agent-fast` -> `ollama/qwen3.6:35b-a3b` (MoE, primary agentic)
- `agent-deep` -> `ollama/qwen2.5-coder:32b` (deep coding)
- `research` -> `ollama/qwen3:32b` (research)
- `quick` -> `ollama/qwen2.5-coder:14b` (fast fallback)

## Not done here, and why

- No `research/inference_nodes.yaml` entry for M80s — per
  `research/WQ3798_M80S_LLM_MODEL_SELECTION.md`'s own closing section, adding a `state:
  live`/`state: down` registry row for a box with no OS/NVMe/unit installed yet is
  exactly the "unverified config value" instinct 8 and the doctrine forbid. That entry
  belongs to whichever privileged session actually brings the node up.
- `litellm-aliases.yaml`'s `api_base` is left as an explicit placeholder
  (`PLACEHOLDER_FILL_FROM_lumen_state_py_once_onboarded`), not a guessed tailnet IP —
  same reasoning.
- No live benchmark numbers: there is no reachable node to run `benchmark.py` against.
  `COMPARISON.md` cites only already-measured T5810 figures plus labeled *estimates*
  from the two research docs; it does not fabricate M80s numbers.
