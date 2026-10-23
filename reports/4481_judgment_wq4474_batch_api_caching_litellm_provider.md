# wq#4481 — judgment pass: wq#4474 Anthropic Batch API research (PR #2349)

**Verdict: CONTRADICT.** Two factual errors in the merged doc, each checked against its
primary source by two independent models (claude-sonnet-5@high verifier, claude-opus-5@high
escalation, same findings). The doc's central architectural conclusion — the Batch API
cannot serve fworker/OpenHands/aider-style multi-turn agent loops, only single-shot
no-feedback workloads — is not disputed and stands.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/2349 (merged `59674484`, this
branch's copy `7640efb8`). Diff is one new file,
`ops/wq4474-anthropic-batch-api-research/README.md` (124 lines, docs-only, no code/probe).
Read cold against `/home/lumen/foreman` main, not the PR's own framing of itself.

## Verification performed

1. **Read the shipped README in full.** Confirmed the diff is exactly one new file, no
   other changes (`git show --name-status 7640efb8`).
2. **Claim A — prompt caching in batches.** README line 46-47 ("Model/feature support"
   bullet): "Prompt caching is NOT supported inside a batch request (an ephemeral cache
   entry would likely expire before a same-batch follow-up ran)." This also contradicts
   the doc's *own* line 26-27: "The discount stacks with prompt-caching discounts."
   Fetched `https://platform.claude.com/docs/en/build-with-claude/batch-processing` live
   (`fleet-search read`, HTTP 200, same day the author claims to have verified against —
   2026-10-23) — section "Using prompt caching with Message Batches": *"The Message
   Batches API supports prompt caching... The pricing discounts from prompt caching and
   Message Batches can stack... because batch requests are processed asynchronously and
   concurrently, cache hits are provided on a best-effort basis. Users typically
   experience cache hit rates ranging from 30 percent to 98 percent..."* **CONTRADICTED.**
   Caching is supported (best-effort, not guaranteed) — the README's flat "NOT supported"
   is wrong, and internally inconsistent with its own stacking-discount line.
3. **Claim B — LiteLLM backing-provider claim.** README "LiteLLM integration" section:
   "LiteLLM has first-class Batches support... with Anthropic as one of the backing
   providers." Fetched `https://docs.litellm.ai/docs/batches` live (HTTP 200) — the
   feature table reads: *"Supported Providers | OpenAI, Azure, Vertex, Bedrock, Mistral,
   vLLM, xAI"*. The string "anthropic" occurs zero times on the page. Bedrock (AWS-hosted
   Claude) is listed; the direct `anthropic/` provider at `api.anthropic.com` — what this
   fleet actually uses (`ops/wq4258-fleet-consolidation-plan/litellm_config_snippet.yaml`:
   `model: anthropic/claude-sonnet-5-5`, `api_key: os.environ/ANTHROPIC_API_KEY`) — is
   not. **CONTRADICTED.**
4. **Independent escalation (opus-5@high, fresh instance, no access to this document or
   the sonnet verifier's conclusions)**: re-fetched both pages itself, reproduced the same
   two quotes, same two CONTRADICTED verdicts. It additionally surfaced a corroborating
   secondary source — a LiteLLM GitHub issue/error reference
   (`litellm/llms/anthropic/batches/transformation.py:92`,
   `NotImplementedError: "Batch creation not yet implemented for Anthropic"`) — consistent
   with the provider table, i.e. calling `litellm.create_batch` against `anthropic/` would
   actively fail, not just be undocumented. It could not fully confirm that specific
   source against installed package code (permission denial on container/source search),
   so it is flagged as corroborating, not independently load-bearing.
5. **Claim C — severity context for Recommendation #2.** The README's one concrete
   "batchable" example is `judge/judge_bench.py`. `DEFAULT_MODELS` there
   (`judge/judge_bench.py:28`) is `"isis-qwen3-32b,qwen36-local,hathor-llama8b"` — three
   local models, zero Anthropic/Claude models. The fleet's only committed Claude-via-
   LiteLLM routing (`wq4258-fleet-consolidation-plan/litellm_config_snippet.yaml`) is an
   unmerged snippet, header-noted as blocked on `ANTHROPIC_API_KEY` landing; the harness's
   actual Claude calls go through the `claude` CLI over OAuth, bypassing LiteLLM entirely.
   So the one example offered for "point this at LiteLLM's `/v1/batches` instead" spends
   nothing on Anthropic today, and the route it names isn't a supported LiteLLM backend
   per Claim B. This doesn't change the CONTRADICT verdict (already established on A/B)
   but means Recommendation #2 is unsound as written, not just the LiteLLM-Anthropic
   sentence in isolation.

## Severity assessment

Not fabricated-evidence (Tier-0 instinct 7 checked): both contradicted claims cite real,
live, fetchable pages that both models independently retrieved and quoted; the error is in
what the author's research concluded from those pages, not an invented source. Both errors
sit in sections that back concrete recommendations (the "easy to wire into LiteLLM" bottom
line, and Recommendation #2's build-ticket proposal) rather than in throwaway color, so they
would mislead a reader deciding whether to act on this research. The doc's core
"architecturally incompatible with agent loops" finding, the API mechanics (limits,
turnaround, custom_id format, 24h expiry, 29-day result retention — all independently
re-confirmed against the same live doc), and the pricing-caveat framing (not guessing a
savings number) are correct and should stay.

## Conclusion

CONTRADICT, escalated per protocol, opus-5@high independently reproduced both findings
rather than deferring to the sonnet verifier. No re-implementation performed here — per
this ticket's instruction and fleet precedent (wq#2601/wq#2603, wq#4284/wq#4291,
wq#4375/wq#4384): the verify/judgment ticket reports, a separate ticket applies the
forward-fix. Filed wq#4495 (build lane, deps=4474) to correct the two claims and soften/
drop Recommendation #2's `judge_bench.py` example, referencing this report's exact quotes
and URLs so the fix ticket doesn't have to re-derive them.
