# wq#3332 Phase 2b — stage chat-digest-local on the Ollama nodes

Phase 2 (`bin/chat-export`, this branch) parses claude.ai `<chat>` blocks into a JSON archive
on Lumen. Phase 2b stages the LOCAL digest half of the pipeline: a copy of the archive is
synced down to an Ollama fleet node and summarized there by that node's own model, so chat
bodies never leave the node for the digest step (chat-digest on Lumen only ever gets heuristic
keyword extraction from the same text — this is the richer, local-LLM alternative).

## Correction to the ticket's node list

The ticket names isis/hathor/nephthys/maat as "the Ollama nodes." Verified against
`research/inference_nodes.yaml` (the node registry `harness/inference_registry.py` lints) and
`lumen_state.py current <node>` before writing anything here, per doctrine — two of the four
are not valid targets today:

- **maat**: not an inference node at all. `lumen_state.py current maat` gives
  `maat.role = Postgres + Vaultwarden node`; it does not appear in
  `research/inference_nodes.yaml` under `nodes:`. Staging an LLM digest job here would compete
  with the fleet's Postgres primary and Vaultwarden instance for no reason — skipped.
- **isis**: `research/inference_nodes.yaml:105-106` has `isis.state: down  # out of service,
  wq#2140: probe fails, 0 slots`. Its model (`qwen3.6:35b-a3b`) was also moved off Ollama onto
  `llama-server` directly (`isis.qwen36_serving` in lumen_state, 2026-08-27) — even once isis is
  back in service, `chat-digest-local`'s Ollama HTTP call would need a different serving path.
  Skipped until wq#2140 is resolved; this kit does not depend on isis.

Actual targets, per `research/inference_nodes.yaml:43-105`:

| node     | state | ollama model   | port  | notes |
|----------|-------|----------------|-------|-------|
| hathor   | live  | llama3.1:8b    | 11434 | always-on, but its 2 slots are dedicated to the judge (wq#2796) — see below |
| nephthys | live  | qwen3:14b      | 11434 | warm, zero historical inference traffic (lumen_state `nephthys.inference_usage`) — the natural first target |

## hathor contention note

`research/inference_nodes.yaml` sets `local_worker_group: false` on hathor's model specifically
so batch/fworker traffic can't land on its 2 ollama slots and contend with the synchronous
judge call (wq#2796). `chat-digest-local` is exactly that kind of batch traffic. **Do not
install the pull timer on hathor.** The CLI itself works there (manual, ad hoc runs only,
during a window with no judge traffic); `install-chat-digest-local.sh` below only wires up the
automatic timer on nephthys.

## What's in this kit

- `chat-digest-local` — the CLI. Reads a JSON archive (`bin/chat-export`'s format), POSTs each
  chat body to `http://localhost:11434/api/generate` on the node it runs on, masks secrets
  before *and* after the model call, and prints one prose digest per chat. `--dry-run` builds
  every prompt without calling Ollama (what `tests/test_chat_digest_local.py` exercises — no
  live node needed to verify the parsing/masking logic).
- `chat-digest-pull.sh` — runs ON nephthys (hourly timer). Pulls Lumen's archive via rsync,
  runs `chat-digest-local` against it, writes the result to `~/chat-digest-local/status/`.
  Pull-based and read-only against Lumen, same single-writer shape as
  `ops/wq3296-neith-bringup/neith-memory-pull.sh`.
- `chat-digest-pull.service` / `.timer` — systemd units for the above.
- `install-chat-digest-local.sh` — run ON nephthys as the lumen user, from a copy of this kit
  dir. Installs the CLI to `~/bin`, the pull script to `~/bin`, and enables the timer.

## Bring-up (nephthys)

1. Verify the Lumen SSH target against `lumen_state.py current lumen` first — never assume the
   tailscale IP in `chat-digest-pull.sh` hasn't changed (same caution as wq#3296's kit).
2. Copy this directory to nephthys, then on nephthys:
   `bash install-chat-digest-local.sh`
3. First verification run:
   `systemctl --user start chat-digest-pull.service && cat ~/chat-digest-local/status/pull.json`
4. Confirm `ollama_version`/model match `research/inference_nodes.yaml` (`qwen3:14b`) before
   trusting output — a node that drifted its served model would still answer, just not with the
   model this kit assumes.

## Verifying the staged kit without a live node

`tests/test_chat_digest_local.py` runs `chat-digest-local --dry-run` against a synthetic
archive fixture: it checks archive loading, title/id fallback, secret masking into the built
prompt, and the `--model` override, all offline. It does not and cannot exercise the real
`/api/generate` call — that needs nephthys itself and is a bring-up-time check (step 3 above),
not a unit test (an unpinned live-node probe is not re-runnable evidence).
