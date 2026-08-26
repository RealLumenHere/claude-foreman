# PLAYBOOK: llm-jobs (v1, 2026-08-26, wq#959)

Domain rules moved verbatim from `instincts.yaml` (tier-2 restructure, R1 §6). Trigger:
building or routing any LLM job — batch, unattended, rung selection, Claude access.
The tier-2 index entry `llm-jobs-domain` points here.

## Rung routing
(added ≤2026-08-13)

Never route consolidation/extraction/batch LLM jobs through messenger-chat/re rungs.

## Unattended JSON jobs fail closed
(added ≤2026-08-13)

Unattended LLM-JSON jobs: `response_format=json_object` + tolerant parse + one retry;
fail closed.

## Model licensing preference
(added ≤2026-08-13)

Prefer mainline + permissive-license models for fleet rungs.

## max_tokens budgets thinking
(added ≤2026-08-13)

Never size max_tokens for a reasoning model by answer length alone; budget for thinking
tokens.

## How Claude access actually works
(added 2026-08-20/21, "CRITICAL ARCHITECTURAL RULE — Frank has corrected this
REPEATEDLY". A7 C6 disputes the wrapper claim's factual accuracy (see A7 §2 R2); the
dispute is Frank's to resolve — text kept verbatim, do not act on either version
without checking with the orchestrator record.)

CLAUDE ACCESS IS NOT VIA ANTHROPIC API KEY. The fleet does NOT use ANTHROPIC_API_KEY
for inference. There is NO pay-per-token Anthropic billing. Claude is accessed via a
WRAPPER AROUND THE CLAUDE DESKTOP APP running on Lumen. The $200/month Claude Max 20x
is a session-based web/desktop/mobile plan — NOT the Anthropic API platform. The API
platform / API key is for Claude Code which we do not use. The wrapper communicates
with the Claude desktop app the same way the Messenger communicates — it knows the
internal calls. Do NOT add anthropic/ model entries with api_key to LiteLLM config. Do
NOT suggest topping up Anthropic API credits. Do NOT calculate per-token costs for
Claude. When Claude inference is needed, it routes through the wrapper, not through
api.anthropic.com. DeepSeek is a SEPARATE paid service used ONLY for engineering tasks
needing a looser model. Everything else should use the wrapper (Sonnet for
chat/messenger, Opus for reasoning, Fable when allowed by Anthropic).
