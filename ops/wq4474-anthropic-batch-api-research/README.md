# wq#4474: Anthropic Message Batches API — research for overnight agent workloads

## Bottom line

The Batch API is real and cheap, and is reachable directly via the Anthropic SDK —
but it is not reachable through LiteLLM's `/v1/batches` for Claude (see "LiteLLM
integration" below), and it cannot serve the workload the ticket names. It batches
independent, single-turn Messages requests; it has no mechanism to feed a tool
result back into the same request and get the next step asynchronously. fworker's
BUILD queue (this session included), OpenHands, and aider are all multi-turn
tool-use loops — each step depends on the previous tool's output, so there is
nothing to submit-and-walk-away-from until the loop ends. **Savings apply to
single-shot, parallel, no-feedback workloads only** (structurally, e.g. the
pattern in `judge/judge_bench.py:53-89` of running one independent judge verdict
per case across a fixed eval set — though that specific script currently calls
only local models, not Claude; see "What's actually batchable" below), not to
agent sessions. Applying the 50% discount to the $200/mo figure as if it covered
everything would overstate savings; the real number depends on what fraction of
spend is non-agentic single-shot Claude calls, which this research did not have
billing data to measure (not guessing it — doctrine: verify, don't guess a root
cause/number).

## How the Message Batches API works

Verified against the live docs (`platform.claude.com/docs/en/build-with-claude/batch-processing`
and `.../about-claude/pricing`, fetched via `fleet-search read`/`web`, 2026-10-23):

- **Discount**: 50% off standard price on input tokens, output tokens, and any
  special pricing (e.g. cache writes/reads) for every request in the batch. The
  discount stacks with prompt-caching discounts.
- **Submission**: `POST /v1/messages/batches` with a `requests` array; each item
  has a `custom_id` (`^[a-zA-Z0-9_-]{1,64}$`, used to match results back to
  requests) and a normal Messages `params` object. Each item is an independent,
  single Messages call — no shared conversation state across items, no tool-result
  continuation handled by Anthropic.
- **Limits**: 100,000 requests or 256 MB per batch, whichever hits first.
- **Turnaround**: most batches finish inside 1 hour; results become available
  either when all items finish or at 24 hours, whichever comes first. A batch that
  hasn't finished by 24 hours expires (incomplete items come back `expired`).
- **Collection**: poll `GET /v1/messages/batches/{id}` for `processing_status`
  (`in_progress` → `ended`) and per-item `request_counts`; once ended, fetch the
  `results_url` and stream back a `.jsonl`-shaped result per `custom_id`.
  Results stay downloadable for 29 days after batch creation, then the batch
  record remains but results are gone. `DELETE /v1/messages/batches/{id}`
  removes it early (cancel first if still in-progress).
- **Model/feature support**: all active models; batching covers vision, tool use
  (including server tools), system prompts, multi-turn conversation *content*
  (i.e. you can submit a full transcript as one item), and extended thinking.
  Prompt caching IS supported inside a batch request: per
  `platform.claude.com/docs/en/build-with-claude/batch-processing` ("Using prompt
  caching with Message Batches"), cache hits are best-effort rather than
  guaranteed, since batch requests are processed asynchronously and
  concurrently — typical hit rates run 30-98%. This is consistent with the
  stacking-discount line above: the batch discount and the prompt-caching
  discount both apply, and caching isn't disabled, just not deterministic.
- **Scope**: batches are scoped to a Workspace; rate limits apply both to the
  Batches HTTP endpoints and to in-flight requests per batch.

Current base pricing relevant to savings math (verified via `fleet-search web`
against Anthropic's own pricing/model-announcement pages, 2026-10-23): Claude
Sonnet 5.5 is $2/MTok input, $10/MTok output; Claude Opus 5.5 is $4/MTok input,
$20/MTok output (the $8/$40 figure that shows up in some search snippets is Opus
5.5's Fast-mode premium price, not standard — resolved by cross-checking the
Anthropic blog post against the platform docs table and the 20%-cheaper-than-Opus-5
framing both give $4/$20). Batch halves all of the above.

## LiteLLM integration

LiteLLM's Batches support does NOT cover Anthropic. `docs.litellm.ai/docs/batches`'s
own supported-providers table lists only OpenAI, Azure, Vertex, Bedrock, Mistral,
vLLM, and xAI — the string "anthropic" appears zero times on that page, and Bedrock
(AWS-hosted Claude) is not the same as the direct `anthropic/` provider this fleet
actually uses (`ops/wq4258-fleet-consolidation-plan/litellm_config_snippet.yaml`:
`model: anthropic/claude-sonnet-5-5`, `api_key: os.environ/ANTHROPIC_API_KEY`).
Calling `litellm.create_batch` against the `anthropic/` provider does not work. For
Claude specifically, the real batch-integration path is the Anthropic SDK's own
`messages.batches` endpoints directly against `api.anthropic.com`, not LiteLLM's
`/v1/batches`. LiteLLM's Batches API remains useful for the providers it does
support (e.g. an OpenAI- or Bedrock-fronted batch workload), just not for this
fleet's Anthropic traffic.

## OpenHands / aider: not batch-compatible

Searched both projects (`fleet-search web/github`) for batch-mode or async-Batches
integration — no hits in either codebase or issue tracker. This matches the
architecture, not a gap either project could close without Anthropic changing the
API: both drive a synchronous agent loop (model call → tool call → tool result →
next model call), and the Batches API has no way to accept a tool result mid-batch
and continue — each batch item is a complete, independent Messages request that
returns once, up to 24h later. The only way to "batch" an OpenHands/aider run would
be to pre-script every turn with no real tool execution in between, which is not
what either tool does. Same reasoning applies to fworker's own BUILD-queue
dispatch (the mechanism running this ticket): each ticket is a Claude Code session
making tool calls against live repo state turn-by-turn, not a single prompt.

## What's actually batchable in this fleet

Not every LLM call here is an agent loop. `judge/judge_bench.py:53` (`run_model`)
loops over a fixed case list and calls `call_judge(model, case)` once per case with
no dependency between cases or on any tool result — structurally the "large-scale
evaluation" shape Anthropic's own docs call out as the target use case for the
Batch API. However, `judge_bench.py`'s own `DEFAULT_MODELS`
(`judge/judge_bench.py:28`) is `"isis-qwen3-32b,qwen36-local,hathor-llama8b"` —
three local models, not Claude — so today it has zero Anthropic spend to batch.
The same caveat likely applies to `judge/judge_bench_drift.py` and any other
nightly/scheduled single-shot classification or scoring pass: the *shape* fits
Batch API, but whether a given run actually calls Claude needs checking before
counting it as a batchable-Anthropic-cost candidate.

## Estimated savings

No per-workload billing breakdown was available to this research (and doctrine is
explicit: don't guess a number you can't verify — report the formula instead).
Savings = 50% × (monthly spend attributable to single-shot, non-agentic,
overnight-tolerant calls). Against the stated $200/mo total: every $1 of spend
that's actually batchable (judge-bench-style evals, not agent sessions) saves
$0.50/mo. If the judge-bench/drift-style workloads are a small slice of the $200
(most of it is almost certainly agentic BUILD/verify/Pokemon/Fable traffic per the
architecture finding above), the realistic monthly saving is a few dollars, not a
$100 headline number — getting the real figure requires tagging judge-bench-style
calls in LiteLLM's spend logs and summing their actual token cost, which this
research ticket did not have access to (no LiteLLM spend-log query tool in this
worker's allowed dirs).

## Recommendation

1. Don't attempt to route fworker BUILD/Pokemon/Fable agent traffic through the
   Batch API — architecturally incompatible, not a config problem.
2. `judge_bench.py` is not a sound example of batchable Anthropic cost today:
   its `DEFAULT_MODELS` (`judge/judge_bench.py:28`) is
   `"isis-qwen3-32b,qwen36-local,hathor-llama8b"` — three local models, zero
   Anthropic spend. And per the LiteLLM correction above, `/v1/batches` doesn't
   back Anthropic anyway. If a fixed-case, no-tool-use eval loop is later found
   that *does* call Claude, batch it via the direct Anthropic SDK
   `messages.batches` endpoint, not LiteLLM's `/v1/batches`. This is a build
   ticket on its own, contingent on finding such a call site, not something to
   do inside a research ticket.
3. Before that build ticket, pull real LiteLLM spend-log numbers for those
   specific call sites to size the actual dollar savings rather than estimating.
