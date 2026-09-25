# research-council (wq#2628)

Sends one hard prompt to several models in parallel through the LiteLLM gateway, then has
a chairman model combine the answers into one report that marks where the models agree and
where they disagree. The pattern follows [karpathy/llm-council](https://github.com/karpathy/llm-council),
minus the peer-ranking stage.

```
research-council "Should we shard queue.db or move to Postgres?"
echo "$PROMPT" | research-council -
research-council --models gemini-flash,deepseek-v4-pro,kimi-k3 "..."
research-council --json "..."        # full run record on stdout
research-council --dry-run "..."     # show seats and chairman, call nothing
```

`/home/lumen/bin/research-council` is a symlink to `council.py`. Run it on Lumen, since the
gateway is `127.0.0.1:4000` there.

## How it works

1. **Fan-out.** Every enabled seat in `council.yaml` answers at the same time. A seat that
   errors or times out is dropped and listed as `no answer`. A 429 gets one retry after
   `retry_429_s`.
2. **Chairman.** If at least `min_responses` seats answered, the first model in `chairman:`
   that answers and returns all required sections (`## Consensus`, `## Disagreements`,
   `## Synthesis`) writes the report. The answers are labelled Response A/B/C with no model
   names, so the chairman judges content, not brand. A chairman whose seat already failed is
   skipped without a call.

Each run writes a JSON record (prompt, every seat's answer or error, chairman attempts,
synthesis) to `/home/lumen/logs/council/` (`--out-dir` or `COUNCIL_OUT_DIR` to change it).

Exit codes: 0 ok, 2 usage/config error, 3 too few seats answered, 4 no chairman met the
section contract.

## Seats

Edit `council.yaml`: add, remove, or swap gateway aliases. Nothing in the code names a
model. Current seat status is in the yaml header. In short: the claude seat is down until
claude-shim is back on :8091 (ISSUE-LOG #733), gemini is the free-tier flash until wq#2627
lands, and the gpt seat is gpt-oss-120b on Cerebras because the gateway has no hosted-GPT
alias yet.

Key: `LITELLM_API_KEY`, else the first readable file in `COUNCIL_KEY_FILES` (default
`council.key`, then `prompt-forge.key`, under `/home/lumen/.secrets/litellm-service-keys/`).

## Example

`examples/2026-09-25-queue-db-sqlite-vs-postgres.md` is the first live run: 3 of 4 seats
answered (claude timed out), deepseek-v4-pro chaired, 326s total.

Tests: `python3 -m pytest tools/council/tests`.
