# PILOT headroom_litellm — REPORT

ticket: wq#4890 | date: 2026-11-18 PT | model: deepseek-v4-flash via LiteLLM :4000

## Verdict: SKIP

Hypothesis falsified. headroom proxy adds latency and token overhead for zero compression
on LiteLLM's OpenAI-compatible API with firmware decompile workloads.

## Measured results

### Test 1 — Plain chat (user message with decompile output)

| Metric | Baseline | Treatment | Delta |
|---|---|---|---|
| Prompt tokens | 1,129 | 1,129 | 0% |
| Completion tokens | 2,048 | 2,761 | +34.8% |
| Total tokens | 3,177 | 3,890 | +22.4% |
| Latency | 11.17s | 17.52s | +6.35s (+57%) |
| Requests compressed | — | 0 | — |

### Test 2 — Tool-use format (tool_calls + role=tool messages)

| Metric | Baseline | Treatment | Delta |
|---|---|---|---|
| Prompt tokens | 1,450 | 1,565 | +7.9% |
| Completion tokens | 1,024 | 1,703 | +66.3% |
| Total tokens | 2,474 | 3,268 | +32.1% |
| Latency | 5.28s | 9.77s | +4.49s (+85%) |
| Requests compressed | — | 0 | — |

headroom stats for both: `requests_compressed: 0`, `no_compressible_content: 1`,
`total_tokens_removed: 0`, `tool_schema_tokens_saved: 0`.

## Root cause

headroom's compression engines are Anthropic-API-first. The OpenAI pipeline
(`/v1/chat/completions`) does not activate SmartCrusher (JSON compression) or
tool-schema deferral on OpenAI-format requests. LiteLLM serves OpenAI-compat only.

## Anomalies

1. **Completion token inflation**: Treatment exceeded max_tokens in both tests (2048→2761,
   1024→1703). Proxy may modify request parameters.
2. **Prompt token inflation (Test 2)**: 1450→1565 (+115 tokens). Proxy adds overhead.

## Prior art context

| Eval | API | Result |
|---|---|---|
| wq#633 | Anthropic (Max) | SmartCrusher viable but hook point reaches 0.07% of bytes |
| wq#889 | Anthropic (Max) | OAuth passes; 92% savings from tool-schema deferral |
| **This pilot** | **LiteLLM (OpenAI)** | **0% compression, +57-85% latency** |

## Full report

`/home/lumen/scratch/research/headroom_litellm_pilot.md`

RECOMMENDATION: SKIP
