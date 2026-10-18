# wq#4212: isis qwen3.6:35b-a3b loaded at num_ctx 4096

LiteLLM `lumen/code` and `isis-coder-14b` both route to `ollama/qwen3.6:35b-a3b`
on isis (100.100.129.24:11434). The tag carried no `num_ctx`, so ollama 0.34
loaded it at `context_length 4096` (`/api/ps`) and truncated every longer prompt
without an error: 3000 words evaluated as 2050 prompt tokens. An agent loop
loses its system prompt and tool definitions once it passes ~2k tokens.

## Fix (applied live 2026-10-18)

`num_ctx 32768` is now a parameter of the tag itself, rewritten in place through
ollama's `/api/create` (`from` = the same tag, all existing parameters kept).

Why the tag and not `num_ctx` in `litellm_params`:
- wq#4101 moves the ollama qwen3 rungs to ollama's OpenAI-compat `/v1` path,
  which has no way to pass `num_ctx`. A LiteLLM-side param would be lost then.
- `harness/hallucination_guard.py` calls the same tag directly. A per-request or
  separate-tag context makes ollama load a second 22GB copy (isis has 64GB and
  runs this model CPU-only, `size_vram 0`).
- No LiteLLM config edit, so no gateway restart.

KV cost is small: 10 of 41 layers are full attention (`full_attention_interval 4`),
2 KV heads x 256 dims, about 20KB/token, so ~0.7GB at 32k.

## Files

- `set_num_ctx.py --check | --apply [--num-ctx N] | --rollback --num-ctx 4096`
  verifies after writing that `num_ctx` landed and no other parameter changed.
- `ctx_probe.py --words N` is the gate: it sends N numbered words to `/api/chat`
  and FAILs if `prompt_eval_count < N`. Live on 2026-10-18 with `--words 3000`, it
  returned FAIL before the fix (2050 tokens, 162s) and PASS after (13918 tokens,
  629s). `/api/ps` now reports `context_length 32768`, with one copy loaded.
- `tests/`: parsing, create-body, and verdict tests over the captured pre-fix
  parameters.

## Caveats

- `ollama pull qwen3.6:35b-a3b` on isis replaces the manifest and drops
  `num_ctx`. Re-run `set_num_ctx.py --apply` after any pull.
- Prefill on isis is ~32 tok/s on CPU. The context is no longer truncated, but a
  Cline-style loop (98k input tokens for a trivial task) still takes many minutes.
  Agent loops should route to a hosted model; lumen/code suits short single calls.
