# wq#3543 — gemini-flash 403 PERMISSION_DENIED (project denied access)

2026-10-07. `docker logs litellm` showed `GeminiException 403 Your project has been denied
access. Please contact support.` on every `gemini-flash` call, with no fallback group
configured, so callers got a hard 403 instead of a routed answer.

## Decision (autonomous, per Instinct 3)

Two options were on the table: fix at the Google console (regenerate the API key — a
physical act only Frank can perform) or retire the rung / point callers at
`deepseek-v4-flash`. Both, actually:

1. **Root cause is already tracked and queued.** wq#3525 (filed automatically by
   fleet-selfheal, blocked, claimed by `burn-opus-3-180271`) diagnosed the same failure:
   `gemini-ai-studio.key` is malformed — 53 chars with an `AQ.` prefix, not the expected
   39-char `AIza...` Google AI Studio shape — so the upstream call 404s at
   `generativelanguage.googleapis.com`, which LiteLLM surfaces as this ticket's 403.
   That ticket already queues the physical fix (Frank regenerates the key at
   aistudio.google.com/app/apikey, writes it to `/home/lumen/.secrets/gemini-ai-studio.key`,
   updates `gemini.env`, restarts `litellm`). This ticket does not duplicate that queue
   entry — re-filing the same Frank-gate twice adds noise, not progress.
2. **This ticket's own scope — stop callers 403ing in the meantime — is fixable now,
   without Frank.** `fragments/gemini-flash-fallback.yaml` adds a `router_settings.fallbacks`
   entry routing `gemini-flash` to `deepseek-v4-flash` (same cheap-tier pool per
   `research/GEMINI_ACCESS.md`), so a 403 on the primary rung routes instead of hard-failing
   the caller. This mirrors the fallback shape already staged (unmerged) by wq#2627/wq#2471
   for the same reason.

Full retirement of the `gemini-flash` rung itself is not warranted: wq#2627 already
decided the Gemini-via-paid-AI-Studio-key path is the sanctioned long-term one
(`research/GEMINI_ACCESS.md`), and wq#3525 shows the only defect is a bad key, not a
structural problem with the rung. Once wq#3525's key rotation lands, fleet-selfheal
re-enables the rung on the first good probe and the fallback above simply stops
triggering — no further action needed either way.

## What's here

| File | Purpose |
|---|---|
| `fragments/gemini-flash-fallback.yaml` | `router_settings.fallbacks` entry: `gemini-flash -> [deepseek-v4-flash]`. Hand-merge into the live `/home/lumen/agent/litellm_config.yaml` per the wq#1617 fragment-merge tool (`merge_litellm_fragment.py merge <live> <this file>`); do not overwrite the live file wholesale. |

## Verification

The live LiteLLM config (`/home/lumen/agent/litellm_config.yaml`) is guard-blocked from
BUILD-worker reads (contains a literal Langfuse secret, wq#2951) and sits outside this
worktree, so this ticket cannot apply/merge the fragment or restart `litellm` itself —
that hand-merge step is the same "needs direct access to the live file" constraint every
prior litellm-config ticket (wq#897/948/1295/1406/1590/2627/2471) hit. The fragment is
staged here, reviewed, and ready for whoever next does a litellm config hand-merge.
