# wq#2137 — serve qwen3.6-35b-a3b via llama-server on hathor + nephthys (clone the isis unit)

Design for making the `local-worker` LiteLLM group three copies of the isis
serving unit instead of three different models. Companion to
`research/DISPATCH_REBUILD_SPEC.md` §2.2 (which already specs the
`local-worker` group shape) and `research/LOCAL_MODEL_STRATEGY.md` §5.1 (which
already argued llama-server over Ollama for this model). This doc adds the
per-box serving plan and the go/no-go gate.

## 1. Why: the wq#2127 measurements (2026-09-20, identical MacPro6,1, CPU-only)

| box | model today | prompt eval | decode | 4k-token prompt TTFT |
|-----|-------------|-------------|--------|----------------------|
| hathor | llama3.1:8b (ollama) | 13.6–15 tok/s | — | ~5 min |
| nephthys | qwen3:14b (ollama) | 8 tok/s | — | ~8 min |
| isis | qwen3.6-35b-a3b (llama-server) | ~63 tok/s | 10.9 tok/s | ~1 min |

Source: ticket wq#2137 / `lumen_state` key `isis.qwen36_bench`. Same hardware,
so the 4–8x prompt-eval gap is the model+server choice, not the box: the MoE
runs ~3B active parameters per token, and llama-server avoids the Ollama tax
(`research/LOCAL_MODEL_STRATEGY.md:184-189`). Prefill is the binding
constraint for worker prompts (`research/LOCAL_MODEL_STRATEGY.md` §4), and the
MoE is also the *stronger* model — there is no capability/speed trade here.
Ollama 0.32.6 forces a single slot for this arch, which is why isis runs
llama-server directly (`llama-qwen36.service`, :8095) and why the clone must
too.

## 2. Design

### 2.1 Model blob: copy over LAN from isis

The weights are Qwen3.6-35B-A3B Q4_K_M, ~21.7 GB
(`research/LOCAL_MODEL_STRATEGY.md:176`). Do not re-download 2x from the
internet; copy isis's blob:

1. Read the GGUF path from the `ExecStart=` line of
   `/etc/systemd/system/llama-qwen36.service` on isis (path-only; never dump
   env blocks).
2. `rsync -av --partial isis:<gguf-path> <same-path>` to hathor, then
   nephthys (~21.7 GB at LAN speed ≈ minutes, not hours; `--partial` so an
   interrupted copy resumes).
3. `sha256sum` on source and each destination must match before any unit
   starts. A truncated GGUF fails at load, but a silently corrupted one is
   worse — check, don't hope.

### 2.2 Unit: clone the isis unit text verbatim, then apply per-box deltas

Copy `/etc/systemd/system/llama-qwen36.service` from isis byte-for-byte as the
base (same binary path, same port :8095, same flags — the isis unit is the
measured configuration; do not "improve" it during the clone). The only edits
allowed are the per-box slot/ctx/thread values in §2.3. Structure reference
for a llama-server unit in this repo: `ops/wq1337-hapi-bringup/llama-server.service`
(that one is Vulkan/hapi; the isis unit, not it, is the template here).

`systemctl daemon-reload && systemctl enable --now llama-qwen36.service` on
each box, gated by §3.

### 2.3 Slots x ctx per box (32k/slot, from measured RAM)

All three boxes are 64 GB (4x16 DDR3-1866: hathor/isis
`research/LOCAL_MODEL_STRATEGY.md:358`, nephthys
`research/INVENTORY_RECONCILIATION.md:190`). Budget per box, f16 KV worst
case: KV for this arch is ~96 KiB/token (48 layers x 4 KV heads x 128 dim x
K+V x 2 bytes — **verify against the KV-buffer line llama-server prints at
startup on isis before finalizing**), so a 32k slot costs ~3 GiB f16, ~0.9 GiB
if the isis unit already runs `--cache-type-k/v q4_0`.

| box | tenancy | slots x ctx | flags | RAM (f16 KV worst case) |
|-----|---------|------------|-------|--------------------------|
| hathor | dedicated (nothing else on it) | 4 x 32k | `--parallel 4 --ctx-size 131072 --threads 12` | 21.7 wt + 12 KV + ~2 buf ≈ 36 GB of 64 |
| nephthys | dedicated after recommission | 4 x 32k | same as hathor | same |
| isis | co-tenant with ollama.service + ollama-lpp.service | 2 x 32k | `--parallel 2 --ctx-size 65536`, keep current `--threads` | 21.7 + 6 + ~2 ≈ 30 GB, ≥30 GB left for the ollama tenants |

Slots are RAM-cheap but compute-shared: 12 physical cores per box means
prefill throughput divides across concurrent slots, so 4 is a queueing cap,
not a parallel-speed claim. The isis row honors the wq#1623 capacity decision
(`ops/wq1623-qwen36-queue-guard/README.md:17-19`): isis is NOT dedicated;
its upgrade is 2x8k -> 2x32k ctx only, and only after hathor's go gate passes.
The 4,910-token-prompt truncation class of failure dies with 32k slots.

Nephthys caveats, recorded not blocking: inventory listed it
decommissioned/plug-cut on 08-23 (`research/INVENTORY_RECONCILIATION.md:49`)
but wq#2127 measured on it 2026-09-20, so it is back; CPU SKU is E5-2697 v2
vs 2699 v2 in conflict (`research/INVENTORY_RECONCILIATION.md:189`) — confirm
with `lscpu` at bring-up, same core count either way.

### 2.4 LiteLLM deployment swap

In `/home/lumen/agent/litellm_config.yaml` (backup first, per
`research/DISPATCH_REBUILD_SPEC.md:110`): the `local-worker` group's hathor
and nephthys deployments swap from their ollama endpoints (:11434,
llama3.1:8b / qwen3:14b) to OpenAI-compatible `http://<box>:8095/v1`,
`model_info.max_input_tokens` = 32768 minus completion headroom,
`max_parallel_requests` = that box's slot count (4/4/2). Keep the three
per-host model names for pins; keep `router_settings` (least-busy,
cooldown, pre-call ctx check) exactly as the rebuild spec defines them.
Restart the gateway container detached, verify `/health` and one completion
per deployment.

### 2.5 Rollback

Keep the ollama units on hathor and nephthys installed and enabled — do not
uninstall, do not delete their models. llama-server binds :8095, ollama
:11434, so both coexist; idle ollama unloads its model from RAM by default.
Rollback is: restore the litellm_config.yaml backup, restart the gateway,
`systemctl disable --now llama-qwen36.service` on the affected box. Cost of a
rolled-back experiment: 21.7 GB of disk per box, nothing else.

## 3. Go/no-go: warm measurements on hathor first

Hathor is the pilot (dedicated box, so measurements are clean; nephthys and
the isis ctx bump wait for the gate). After blob copy + unit start, run the
same measurement wq#2127 used, **warm** (second run, model resident):

- **GO** (proceed to nephthys, then isis ctx bump, then §2.4 swap): warm
  prompt eval ≥ 50 tok/s and decode ≥ 9 tok/s on a ~4k-token prompt (within
  ~20% of isis's 63 / 10.9 — same hardware should match; hathor is one
  BootROM behind, `research/INVENTORY_RECONCILIATION.md:112`, expected
  irrelevant), AND 4 concurrent 1k-token requests all complete with no slot
  eviction and no OOM in `journalctl -u llama-qwen36`.
- **NO-GO**: any criterion misses. Stop, leave ollama serving as-is, record
  the numbers in `lumen_state` (`hathor.qwen36_bench`) and the divergence in
  ISSUE-LOG — a same-hardware miss means the isis unit encodes something this
  doc failed to capture, which is exactly what the pilot exists to catch.

Record the hathor warm numbers in `lumen_state` as `hathor.qwen36_bench`
either way, mirroring `isis.qwen36_bench`.

## 4. Out of scope

Routing-table changes (which archetypes ride `local-worker`) stay with
`research/DISPATCH_REBUILD_SPEC.md:109`; this doc only makes the group's three
backends identical and honest about their served context.
