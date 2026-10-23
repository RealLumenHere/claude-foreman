# Limpet Agent Processor Specification

> Limpet Project — the agent-core request/response pipeline: tool dispatch, evidence, context assembly
>
> Status: DRAFT v0.1 — 2026-10-23
> Author: Claude (Limpet Architect)
> Ticket: wq#4508

---

## 1. Overview

The "agent processor" is the pipeline a Limpet agent's tool-call loop runs
through: dispatch a call to the meta-server, log evidence, and decide what
the *result* becomes once it is back in the agent's hands. This document
specifies that last step. The dispatch/evidence stages themselves are
implemented in `ops/wq3808-limpet-mcp-meta-server-a1/` (`meta_server.py`,
`evidence.py`) and the trust/plugin-lifecycle rules in
`design/wq3806_limpet_architecture.md` §§1-3; this spec does not restate
those, only the result-handling step that follows them.

## 2. Problem

`design/wq3806_limpet_architecture.md:155-158` already mandates "capped,
structured results... size-capped and summarized before it reaches the
agent's context" and `docs/agent-chat-spec.md:144-147` already mandates that
the UI render a bounded preview plus an evidence path/hash, never the full
blob. Neither has an implementation: `meta_server.py` passes results through
uncapped. A result over a few KB (routine for RE tools: disassembly, strings
dumps, nmap scans) either blows the agent's context budget if inlined, or
loses information if naively truncated at a byte offset mid-finding.

## 3. Bands

Every tool result lands in one of two bands by length alone:

- **Hot** (`len(result) <= HOT_THRESHOLD`, 2000 chars — the fleet's own
  truncation discipline, reused here rather than inventing a second number):
  the result goes straight into the agent's context, verbatim (credential
  masking still applies, §5).
- **Warm** (`len(result) > HOT_THRESHOLD`): the full result is written to
  cold disk under the workspace's evidence tree
  (`<workspace>/evidence/results/<tool>-<sha256>.txt`) and never reaches
  context. A 3-5 line LLM-generated summary takes its place.

There is no separate "cold band" state machine — "cold" names where the full
text lives (disk), not a third context tier.

## 4. Model selection for the summary call

The summary call MUST use a fast model, never the reasoning model the agent
itself may be running on — a summary is a cheap, latency-sensitive side
call, not a reasoning step. This is enforced by routing the call through
`model_router.py`'s existing `resolve_profile()` with the `"speed-critical"`
requirement profile (one of `model_router.REQUIREMENT_PROFILES`), not a new
ad hoc model choice: `result_store.summary_model()` is the one call site.

## 5. Masking

Both the hot-band inline text and the warm-band cold-disk file are passed
through `workspace_report.mask_text()` before being written or returned —
the same credential-masking pass every other Limpet artifact gets (fleet
instinct 4). A result derived from an untrusted target (firmware, APK) is
treated as data, never executed or rendered live (`docs/agent-chat-spec.md`
§4.2's "untrusted-output hygiene").

## 6. Context assembly

### 6.1 Hot-band entries

The masked text itself, unchanged. No evidence reference needed since
nothing was written to disk.

### 6.2 Warm-band entries

The LLM summary, followed by a one-line reference: the cold-disk path and
the content's sha256 — the same `path + hash` shape the UI already shows
for capped results (`docs/agent-chat-spec.md` §4.2's "evidence path and
content hash... open in workspace" affordance). An agent (or operator) that
needs the full output reads the cold path directly; the summary is not a
replacement for the evidence, only for what sits in context.

### 6.3 Warm-band LLM summaries — division of responsibility

Limpet's agent core and its LiteLLM client are still design-only
(`design/wq3806_limpet_architecture.md` §6: "allow routing hard steps to a
fleet model via LiteLLM" — not yet built). `model_router.py` (wq#3968) and
`critic.py` (wq#3966) already established the house answer for "this step
needs a model call but the client that would place it doesn't exist yet":
keep the *decision* pure and offline-testable, and let the call itself be
placed by "the caller, once it exists." `result_store.py` (wq#4508) follows
that same split:

- **This module's job:** classify the result into a band (§3), decide which
  model a summary call must target (§4), mask (§5), write the cold file, and
  — once the caller hands back a summary string — assemble the
  `StoredResult` that `context.py`'s `entry_for()` turns into the final
  context entry.
- **The caller's job (agent core, once it exists):** when
  `result_store.band_for(result) == "warm"`, resolve a model via
  `result_store.summary_model(...)`, place that LiteLLM call itself with a
  prompt asking for a 3-5 line summary, and pass the returned text to
  `result_store.store_result(..., summary=that_text)`.

A warm-band result reaching `store_result()` with no summary yet raises
`ResultStoreError` (fail-closed, the same wq#2630 posture `model_router.py`
uses for an unroutable capability) rather than silently truncating or
inlining the raw text — both of which are exactly the behaviors this
mechanism replaces.

## 7. Implementation

- `limpet/result_store.py` — `HOT_THRESHOLD`, `band_for()`,
  `summary_model()`, `store_result()`, `StoredResult`.
- `limpet/context.py` — `entry_for()`, `ContextEntry`.
- Tests: `limpet/test_result_store.py`, `limpet/test_context.py`.

## 8. Open items / decisions deferred to the agent-core build

- The actual LiteLLM call site, retry/timeout policy for the summary
  request, and the summary prompt text are the agent core's to decide once
  it exists — out of scope here, same deferral `critic.py` and
  `model_router.py` already took.
- Whether `HOT_THRESHOLD` should be configurable per plugin (some RE tools'
  "small" output is still large) is left to a future ticket; today it is a
  single fleet-wide constant, matching the fleet's own 2000-char rule.
