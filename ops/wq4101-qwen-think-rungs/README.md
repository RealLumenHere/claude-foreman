# wq#4101 — lumen/code returns EMPTY content: qwen3 thinking eats max_tokens, truncation masked as `stop`

Found by fleet-health-c8220e 2026-10-16. `lumen/code` → `ollama/qwen3.6:35b-a3b`
@ isis (100.100.129.24) routes and decodes fine (12.77 tok/s, wq#3794), but every
caller whose `max_tokens` is below the model's hidden thinking length gets an
empty string with `finish_reason=stop` — a silent failure for agents and judges.

## Root cause (two defects, both in LiteLLM's native `ollama/` provider path)

1. **Thinking on by default, dropped from the response.** ollama auto-enables
   qwen3 thinking; the thinking tokens count against `num_predict`/`max_tokens`;
   LiteLLM's native-provider transform discards the `thinking` field instead of
   mapping it to `reasoning_content`.
2. **`done_reason=length` masked as `finish_reason=stop`.** The one signal that
   would tell the caller "you were truncated" is rewritten.

Verified live against isis 2026-10-17 (this ticket's design probes):

| probe | result |
|---|---|
| `/api/chat`, `think:false`, 200 tok | content non-empty, 10 eval tokens, 1.9 s |
| `/v1/chat/completions`, `reasoning:{effort:none}`, 200 tok | content non-empty, 15 completion tokens |
| `/v1/chat/completions`, default think, 200 tok (control) | content EMPTY, thinking in `reasoning` field, **`finish_reason=length` reported truthfully** |

So ollama's own OpenAI-compat endpoint has **neither defect**: it reports the
true finish_reason and carries thinking in a `reasoning` field.

## Decision: convert the alias to `openai/` + `extra_body: {reasoning: {effort: none}}`

Ticket options were (a) per-alias think-off vs (b) map thinking into
`reasoning_content` + preserve `finish_reason=length` inside the native path.
(a) via the **compat endpoint** wins because:

- It is the registry-standard shape already in production — nephthys-qwen14b and
  local-worker-nephthys (`research/inference_nodes.yaml:48`, wq#2251).
- Upstream LiteLLM's `think`/reasoning mapping for the native `ollama/` provider
  is broken (BerriAI/litellm #15059, #11680), so (b) means patching/forking the
  provider; the compat route gets truthful `finish_reason=length` for free, which
  covers (b)'s preserve-the-signal half even when a caller still truncates.
- `lumen/code` is a code alias on a 12.77 tok/s rung: 2344 chars of thinking is
  pure latency (31 s for an empty answer vs 5.7 s for correct code with think off).

## What ships here

- `fix_think_rungs.py` — text-surgery patcher for the live config (comments
  preserved, parse-equality validation, timestamped backup; `--check`/`--apply`).
  Converts every `ollama/qwen3*` / `ollama_chat/qwen3*` model_list entry:
  `model` → `openai/<blob>`, `api_base` += `/v1`, `api_key: none` if absent,
  `extra_body: {reasoning: {effort: none}}`. Same-class sweep built in: other
  native-ollama entries are printed as finish_reason-masking SUSPECTS (per-alias
  decision, not auto-converted); qwen3 entries that already carry an `extra_body`
  are printed for HAND-MERGE.
- `probe_think_rung.py` — test-to-failure acceptance probe (the ticket's gate):
  200-token completion through the gateway must return non-empty content or
  `finish_reason=length`. Fails (exit 1) on the wq#4101 class — it would fail
  against the live gateway today, so it can falsify (wq#2630).
- `tests/` — pytest over a committed fixture (11 tests): bucket classification,
  parse-equality, comment survival, idempotency, no-write on `--check`, backup on
  `--apply`, and an outside-scope all-`openai/` config that must pass through
  byte-identical.

## Apply procedure (live config is fleet infra, outside a BUILD lane's sandbox — ISSUE-LOG #515)

```
python3 ops/wq4101-qwen-think-rungs/fix_think_rungs.py --check          # gates only
python3 ops/wq4101-qwen-think-rungs/fix_think_rungs.py --apply          # backup + write
sudo -n systemd-run --unit=litellm-restart-wq4101 --collect --no-block docker restart litellm
python3 ops/wq4101-qwen-think-rungs/probe_think_rung.py --model lumen/code
```

Rollback: restore the printed `.bak.*_wq4101` file and restart the same way.

Note: the default probe key (`foreman-litellm.token`, the scoped foreman-local-rung
key) got HTTP 401 for `lumen/code` when tried from this ticket's lane — that key's
allowlist carries registry-owned names only (`harness/node_render_gateway.py`
`merge_allowlist`). Pass `--key-file` with a key scoped to the lumen/* aliases.
