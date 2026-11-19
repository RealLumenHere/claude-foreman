# wq#5015 - lossless tool_result compression as a LiteLLM pre-call hook

`tool_result_compress.py` re-encodes tool results in a request before LiteLLM forwards it:
OpenAI-shape `role: tool` messages and Anthropic-shape `tool_result` blocks. User and
assistant text is never touched. Filter patterns come from RTK / 9router-go (wq#4978); the tool
itself is rejected (two CVEs, subscription pooling).

## Lossless, proven per call
RTK's own filters are mostly lossy (`git diff` -> `+142/-89, 3 files`). These are re-encodings:
each reversible filter must satisfy `decode(encode(x)) == x` at runtime and come out strictly
shorter, or the original is kept byte for byte. A deliberately lossy encoder patched in is
rejected by that check (verified). Exceptions, garbage input, or a missing `messages` key leave
the request unchanged. Output is a pure function of input, so an unchanged tool result encodes
the same way every turn and provider prompt caches stay warm.

| filter | input shape | encoding |
|---|---|---|
| read-numbered | `     12\tcode` (padded) | numbers unpadded, width in legend |
| read-numbered (opt-in) | `12\tcode` consecutive | numbers dropped, `lines N-M` in legend |
| grep | `path:N:text` | one path header per file, matches indented |
| paths | find / ls -1 / glob / search-list | one `dir/` header per run of siblings |
| tree | `tree` box drawing | 4-col prefixes -> 2-col ASCII |
| git-diff | `git diff` | `diff --git` headers implied by `---`/`+++` replaced by `§` |
| git-status | long `git status` | git's fixed `(use "git ...")` hints dropped (state lines checked identical) |
| dedup-log | runs of >= 3 identical lines | `line ⟨×N⟩` |
| smart-truncate | anything | **LOSSY**, off unless `TOOL_COMPRESS_TRUNCATE_CHARS` is set |

Every compressed result starts with one legend line `[rtk:<filter> ...]` telling the model how
to read it.

## Measured savings (real prompts, 2026-11-19)
`measure.py` over tool_result blocks in Claude Code transcripts on Lumen (last 7 days; the
LiteLLM proxy stores no prompts, so these are the best available sample of tool-heavy turns),
tokens via tiktoken cl100k_base. 80 transcripts, 1,338 tool results, 517,389 tool-result tokens
(61.7% of all message tokens):

| mode | tool-result tokens saved | of all message tokens |
|---|---|---|
| default (all lossless filters, line numbers kept) | 0.8% | 0.5% |
| `TOOL_COMPRESS_READ_NUMBERS=drop` | 6.0% | 3.7% |

Full 7-day corpus (202 transcripts, 3,433 tool results, 1,180,740 tool-result tokens): default
0.6%, drop mode 5.0% (2.9% of all message tokens). git-diff hit once (2.8%).

Per filter, on the results they hit: paths 29.8%, grep 16.8%, read-numbered (drop) 9.2%,
dedup-log 4.0%. git-diff, git-status and tree did not occur in the sample. Exact duplicate tool
results inside one transcript: ~0%.

**The ticket's 20-40% figure is RTK's lossy number and does not hold for lossless encoding on
this corpus.** About 57% of tool-result tokens are file reads, and a file's text cannot be
shrunk losslessly in a form a model can still read. Getting further means accepting loss
(diff-to-stats, code-body stripping, truncation), which this module keeps opt-in.

Run it yourself: `python3 measure.py --days 7 --max-files 80` (aggregates only, no content).

## Activation (not done; needs a gateway recreate)
1. Mount the file: in `/home/lumen/docker/docker-compose.yml`, service `litellm`, add
   `- /home/lumen/agent/tool_result_compress.py:/app/tool_result_compress.py:ro` next to the
   `master_key_guard.py` mount, and copy the file to `/home/lumen/agent/`.
2. In `/home/lumen/agent/litellm_config.yaml` under `litellm_settings:` add
   `callbacks: tool_result_compress.proxy_handler_instance`.
3. Optional env on the service: `TOOL_COMPRESS_READ_NUMBERS=drop`.
4. Back up both files, then `docker compose -f /home/lumen/docker/docker-compose.yml up -d litellm`
   (the gateway is down for the recreate) and confirm `/health/liveliness`.
Kill switch: `TOOL_COMPRESS_DISABLE=1`. Per request: `metadata: {"tool_compress": false}`.

## Tests
`python3 -m pytest test_tool_result_compress.py`: 22 tests. Every filter fires and round-trips;
never grows or raises on 400 random strings and 360 mutated structured inputs; ambiguous inputs
left alone; deterministic; truncate opt-in; hook rewrites OpenAI and Anthropic shapes, leaves
user text, honours the kill switch and per-request opt-out, survives garbage.
