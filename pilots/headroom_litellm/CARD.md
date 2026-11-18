# PILOT headroom_litellm — Headroom context-compression proxy in front of LiteLLM :4000

Experiment card. Generated 2026-11-18 by wq#4890 runner.

| | |
|---|---|
| Proposal | `headroom_litellm` |
| Deferred | 2026-11-17 (from wq#4872 Limpet RE toolchain research) |
| Impact / effort / risk | M / S / S |
| Node | lumen |
| Wall-clock budget | 30 min |
| Token budget | 20000 |
| Report | `/home/lumen/scratch/research/headroom_litellm_pilot.md` |

## Why this is a pilot and not a research prompt

headroom-ai claims 60-95% token reduction. Whether that claim holds for our specific
workload (Ghidra decompile output through LiteLLM's OpenAI-compatible API) is a number
that only a measurement can produce. The prior evals (wq#633, wq#889) tested against the
Anthropic API; LiteLLM serves a different API format.

## Hypothesis

headroom proxy between the caller and LiteLLM :4000 reduces prompt tokens by at least 10%
on a Ghidra decompile analysis workload, with less than 2s latency overhead per request.

## Primary metric

Prompt token reduction percentage (%). Win threshold: >= 10%.

## Fixed sample

Synthetic Ghidra decompile output for Seneca USFS-05-V2 SPI flash read function at
0x08004a20 (two functions + 16 string references, ~700 tokens of decompile content).
Pinned in `pilots/headroom_litellm/runner.py` as `GHIDRA_DECOMPILE_OUTPUT`.

## Baseline (measure FIRST)

```
curl -s -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[...],"max_tokens":1024}' \
  http://localhost:4000/v1/chat/completions
```

## Treatment

```
headroom proxy --port 8787 --openai-api-url http://localhost:4000 \
  --no-subscription-tracking --mode token
# then same curl against http://localhost:8787
```

## Kill criteria

- Wall-clock exceeds 30 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

No fleet data needed. Synthetic decompile output only.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod.
