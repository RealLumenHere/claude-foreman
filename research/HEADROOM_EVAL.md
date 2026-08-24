# wq#633 — Headroom Eval: Reversible Context Compression for Worker Payloads

ticket: wq#633 | agent: claude-web-633-849-7b80f2 | date: 2026-08-23 PT | deps: wq#629

## VERDICT: **CacheAligner-only immediate, SmartCrusher library-mode conditional, proxy BLOCKED on OAuth test**

## 1. What headroom is

chopratejas/headroom (Apache-2.0, 29K+ stars, v0.25+). A local-first context compression
layer that sits between the agent and the LLM provider. Modes: Python library (`compress()`),
transparent proxy (`ANTHROPIC_BASE_URL` redirect), MCP server (exposes tools). Five engines:

| Engine | What it compresses | ML? | Overhead | Claimed savings |
|---|---|---|---|---|
| CacheAligner | Volatile prefix content (timestamps, UUIDs) | no | sub-ms | cache hit rate, not token count |
| SmartCrusher | JSON arrays/objects | no | sub-ms | 60-95% on JSON |
| CodeCompressor | Source code (tree-sitter AST) | no | ~5ms | 15-20% |
| Kompress-v2-base | Prose/unstructured text (ModernBERT 149M) | yes | 20-80ms CPU (P90 576ms ONNX) | 30-50% on prose |
| CCR | Reversible retrieval wrapper | no | ~2ms | enables aggressive compression safely |

Live-zone compression: only compresses NEW bytes (fresh tool output); frozen prefix stays
byte-identical so provider KV cache is not busted.

## 2. Our payload mix (from 8 real worker transcripts, wq#629 baseline data)

285 tool results across fbuild-613/614/625:
- **93.7% JSON dicts** (267/285): bash command outputs (40.6%), file reads (14.8%), other JSON (44.6%)
- 4.9% text, 1% code, 1% short/status
- Sizes: p50=752 chars, p90=7,915 chars, max=48,747 chars inline
- 17 persisted overflows (35-129KB each, 1.4MB total) — these are the claude_context.sh dumps

**Implication: SmartCrusher is the only engine that hits our dominant payload type.** Kompress
covers <5% of our results. CodeCompressor covers ~1%. CacheAligner is orthogonal (prefix
stability, not payload compression).

## 3. Engine-by-engine assessment

### CacheAligner — RECOMMENDED (diagnostic, zero risk)

Our TOKEN_BASELINE shows 89-99% cache hit rate in-session, with zero mid-session thrash.
Cache breaks happen at session boundaries (resume/land files re-prime 120-156K tokens).
CacheAligner won't fix the cross-session re-prime (that's architectural — new process = new
cache chain), but it CAN detect volatile content in our worker prompt/settings that silently
breaks intra-session cache. Run it as a read-only lint against FOREMAN_DISPATCH.prompt and
worker-settings-interim.json. Zero ML, zero proxy, zero risk.

*(Done in wq#887 — `research/cache_aligner_lint.py`, findings in CACHE_ALIGNER_LINT.md. No
volatile content in any target; the one layout defect it did find is worth 0 tokens because
CWD_STABILITY §(i) proved the region is not cacheable.)*

### SmartCrusher — CONDITIONAL (highest value, needs hook point)

> **wq#888 correction (2026-08-24):** the hook point was found and the answer is no.
> The 93.7% below counts the tool-result *envelope*, which claude-code assembles
> after every hook runs. The only usable hook (PreToolUse `updatedInput`) sees raw
> Bash stdout, and that is JSON in **1.7%** of results — 0.07% of tool-result bytes.
> See research/SMARTCRUSHER_HOOK.md. The remaining §3-§5 assessment of the engines
> themselves stands; only SmartCrusher's rank in §5 changes.

93.7% of our tool results are JSON. SmartCrusher claims 60-95% on JSON arrays (collapses
repeated keys, deduplicates homogeneous records, preserves structure). Our bash command
outputs are JSON-wrapped (`{stdout, stderr, exitCode}`) — the stdout inside is often
repetitive text (config dumps, systemctl output, ls listings). SmartCrusher would hit both
the wrapper AND the content if stdout contains JSON.

**The problem: where does the hook go?** Our workers are `claude -p` sessions. Tool results
flow from the tool (Bash, Read, Write) through claude-code's internal plumbing into the
transcript. We don't control that pipeline. Options:
- Library mode in fworker.py: can only compress what fworker touches. fworker doesn't see
  tool results (claude-code handles tool execution internally).
- Proxy mode: transparent, intercepts all traffic. See §4.
- MCP server mode: the agent calls `headroom_compress` explicitly on large results. Opt-in,
  not automatic. The agent has to learn when to compress, which defeats the purpose.

### CodeCompressor — NOT JUSTIFIED

1/285 tool results contain code patterns. Our workers emit code via file writes (Write tool),
not as tool results. The Write tool's content goes INTO a file on disk, not back through the
context. No lever here.

### Kompress-v2-base — NOT JUSTIFIED

149M ModernBERT model. On our Broadwell E5-2699 v4 (no AVX-512, no VNNI — confirmed wq#569):
expect the high end of the 20-80ms range or worse. P90=576ms ONNX is measured on Apple
Silicon, not Broadwell. The wq#569 finding that ONNX-int8 is SLOWER than fp32 in 12/12 cells
on this CPU applies here too. Resident cost ~600MB, violates no-warm-models directive.

Only 14/285 results (4.9%) are prose text. Even 50% compression on 4.9% of payload = 2.5%
total savings. Not worth 600MB resident + uncertain latency on Broadwell.

## 4. Integration surface: proxy mode and the OAuth question

Proxy mode (`headroom proxy start`) redirects `ANTHROPIC_BASE_URL` to localhost. Headroom
intercepts requests, compresses tool-result content blocks, forwards to Anthropic.

**Our specific constraints:**
- fworker.py `build_env()` deliberately UNSETS `ANTHROPIC_BASE_URL` (DeepSeek env-leak
  protection, documented in FOREMAN_DISPATCH.md). A proxy path must set it back, which
  reopens the env-leak class the unset was designed to close. Mitigation: proxy URL is
  localhost, not a cloud endpoint — the leak class is "sends to wrong local process," not
  "sends to wrong cloud API." Still, the var must be scoped tightly.
- Our Max plan uses OAuth bearer tokens (`apiKeySource: "none"` in transcripts). Headroom
  proxy docs cover API key passthrough. OAuth bearer passthrough is not documented. The
  `claude` CLI handles OAuth internally (refresh, session management). A proxy that breaks
  the OAuth flow = dead workers.
- Proxy adds a process to manage. If it crashes, workers hang or fail (depending on
  timeout config). Must be systemd-managed with restart policy.

**BLOCKED: do not adopt proxy mode until a controlled test confirms OAuth bearer passthrough
on the Max plan.** Test procedure: start headroom proxy, run a single `claude -p` short
session through it, confirm the session completes and compression metrics are emitted.

## 5. Fit ranking (our stack, ordered by risk-adjusted value)

1. **CacheAligner diagnostic** — standalone lint, no integration. Zero risk, zero ML, zero
   proxy. Run against worker prompts/settings to find cache-busting patterns. Actionable
   today.
2. **SmartCrusher library-mode** — `pip install headroom-ai` (no `[ml]` extra, ~50MB).
   JSON compression on tool outputs. Needs a hook point in the tool-result pipeline.
   Blocked on finding where to intercept (claude-code internal vs fworker wrapper vs
   proxy).
3. **MCP server mode for orch sessions** — exposes headroom_compress/headroom_retrieve
   tools. Adds ~2-3K tool-schema tokens to prefix. Useful for claude.ai orch sessions
   where the operator can call compress explicitly on large results. Not automatic.
4. **Proxy mode for headless workers** — maximum automation, maximum risk. BLOCKED on
   OAuth passthrough test.

## 6. What we already have (from wq#629 NOT-APPLICABLE analysis)

The TOKEN_BASELINE found our in-session cache hit rate is 89-99% and thrash is zero.
The cost lives at session boundaries (resume re-prime 120-156K). Headroom's live-zone
compression preserves the frozen prefix — but we already get this naturally because
claude-code's prompt caching works the same way. The marginal gain from headroom's
CacheAligner on top of claude-code's existing cache is the VOLATILE CONTENT detection
(timestamps/UUIDs in our prompt that silently bust the cache). That's the diagnostic value.

## 7. Recommendation

- **Now:** Run CacheAligner as a diagnostic on worker prompts. Seed ticket.
- **Next:** ~~Investigate SmartCrusher hook point.~~ DONE, wq#888: hook point exists
  (PreToolUse `updatedInput` + pipe filter), stream-json post-processing is impossible
  (one-way stream), payload reachable is 0.07% of tool-result bytes. Prototype merged
  unwired. research/SMARTCRUSHER_HOOK.md.
- **Later:** Test proxy OAuth passthrough. If it works, evaluate proxy mode for headless
  workers. Seed ticket.
- **Never:** Kompress ML model on Broadwell for <5% of payload. Not worth the resident cost.

## Reproduce

Payload analysis: `python3 /home/lumen/foreman/research/token_baseline.py` (existing).
Headroom install (bench only): `pip install headroom-ai` (no `[ml]` extra).
CacheAligner lint: `python3 -c "from headroom import CacheAligner; ..."` (after install).
