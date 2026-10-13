# wq#3794: Mac LLM fleet reconfiguration — what landed, what didn't

Ticket text and `scratch/research/mac-llm-recommendations.md` describe four symmetric Mac
LLM roles: isis (agent), hathor (judge), wadjet (research), nephthys (embedding). The actual
registry (`research/inference_nodes.yaml`) only serves three inference roles today, and the
fourth name in the ticket does not map onto an LLM host at all. Issue-log row 1280 records this.

## Landed (this branch)

- **isis** (`families.qwen36-local`, `research/inference_nodes.yaml:16-27`): blob swapped from
  `Qwen3.6-35B-A3B-Q4_K_M.gguf` to `Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf` — the MoE-prefill
  problem all three reviews in the research doc agree on. Family id kept as `qwen36-local`
  because `governor_prefixes` is frozen (design §4); the id/blob mismatch is commented in place.
  `context_tokens` raised 8192 -> 16384 to match the node's already-served ctx (`:131`).
  `prefill_tps`/`decode_tps` cleared to `null` (unmeasured) rather than carrying over the old
  blob's bench numbers — those described the 35B MoE, not the new blob.
- **hathor** (`families.hathor-llama8b`, `:28-33`): blob bumped `llama3.1:8b` ->
  `llama3.1:8b-instruct-q8_0` per the ticket's judge-precision ask. `prefill_tps` cleared to
  `null` (stale at the old quant).
- Re-rendered with the registry's own tooling, not by hand: `fleet_node.py render --apply`
  (dispatcher/config.json, research/models.yaml) and `node_render_gateway.py render`
  (ops/inference-registry/litellm_local_block.yaml) — the `render` subcommand only, never
  `apply`/`force-restart`, so nothing live was touched or restarted.
- isis's node `state: down` (wq#2140, probe fails / 0 slots) is untouched — that's a separate
  infra fault, not a model-choice problem, and this ticket doesn't claim to fix it.

## Deferred, not guessed

**wadjet.** Not an LLM-serving node in the current registry or inventory. It's a Mac Pro 6,1
named as the Vaultwarden Postgres HA standby (`research/BIN_TOOL_AUDIT.md:186-187` —
`wadjet-ha-failback.sh`/`wadjet-ha-monitor.sh`; `research/NETWORK_PREMORTEM.md:94` — Ma'at <-
Wadjet Postgres replication) and is listed `standby/off, OFFLINE since 07-19`
(`research/INVENTORY_RECONCILIATION.md:58`). Standing up an LLM research role on it means first
confirming it's reachable and its vault-HA role isn't still load-bearing — a physical/ops check,
not a config edit. Follow-up: re-probe wadjet's tailnet reachability and vault-HA status before
adding it as a `fleet-node add` inference node.

**nephthys.** The research doc's embedding pick (`nomic-embed-text` / `all-minilm`) would
replace `nephthys-qwen14b` (`qwen3:14b`), but that family is the judge's **required fallback
identity**, just pinned by commit 53a9a017 (`fix(judge): pin nephthys-qwen14b identity, require
it on fallback`) landed immediately before this ticket was dispatched. Swapping nephthys to an
embedding-only model in the same pass would silently undo that fix's guarantee. Follow-up:
decide with whoever owns the judge-fallback design whether the fallback identity moves to a
different node first, then repoint nephthys.

## judge/JUDGE_MODEL_IDENTITY.json — deliberately left untouched

`bin/judge` fail-closes on a served-weights mismatch (wq#2821, `judge/judge_common.py:254-279`
`verify_model_identity()`): it POSTs `ident["tag"]` to the node's live `/api/show` and compares
the returned blob digest against the pinned `blob_sha256`. The `hathor-llama8b` entry still
pins `tag: "llama3.1:8b"` and the Q4_K_M digest — intentionally **not** updated to
`llama3.1:8b-instruct-q8_0` in this change, because hathor is not actually serving that tag yet
(this ticket only edited the config repo; no live Ollama pull/retag happened on hathor). Pointing
the identity pin at a tag the node doesn't yet serve would make every judge call fail closed
the moment this branch is confused for a live rollout. Follow-up ops step, in order: pull/create
`llama3.1:8b-instruct-q8_0` on hathor -> query its `/api/show` -> capture the real blob sha256 ->
update `judge/JUDGE_MODEL_IDENTITY.json`'s `hathor-llama8b.tag`/`blob_sha256` together, same
pass. Never fill in a guessed hash (instinct 8; see the existing `nephthys-qwen14b._pending`
entry in that file for the established "leave it empty and fail closed" pattern).

## Not yet benchmarked

The ticket calls for testing 20 representative tickets before committing to the new isis/hathor
blobs in production. That bench is outside a single config-edit ticket's scope — `prefill_tps`/
`decode_tps` were left `null` (this repo's "unmeasured" convention) rather than filled with the
research doc's planning-range estimates, which vary 2-3x across its own three source reviews
(compare `scratch/research/mac-llm-recommendations.md` lines 99-100, 429, 511 for the 14B
Q4_K_M row alone). Follow-up: run the bench, then fill in real numbers via the registry (not by
hand-editing the generated files).
