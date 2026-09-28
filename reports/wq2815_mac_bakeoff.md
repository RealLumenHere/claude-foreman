# wq#2815: Mac local-rung model bake-off

2026-09-28, attempts a1 (03:11-04:11Z, killed at the 3600s wall clock) and a2 (04:11Z-).
Decision rule (ticket, binding): adopt the first candidate with >=2/3 F-canaries SUCCESS or
DEGRADED_SUCCESS inside the first-frame budget; else ingest "local-rung model_class unproven;
no-route until horus" and leave routing as-is (b083782 + wq#2583).

## Verdict

**No candidate has passed. Routing stays as-is (b083782 + wq#2583: `local-worker` stays out of
the mechanical-edit and probe-build chains, `research/models.yaml:351-352`).**
Four of five candidates are eliminated on evidence. qwen3:8b is the only live one: it is the
only candidate that both tool-calls correctly and fits the first-frame budget, but both of its
completed attempts ended TURNS (10-turn ceiling, `error_max_turns`). It had created the file
and not yet committed or opened a PR. Its second attempts and F3 were still running on
nephthys when a2's 3600 s wall clock ran out. wq#2893 (deps wq#2885-2887) applies the binding
rule to the final verdicts, then either adopts qwen3:8b or ingests the no-route fact and
restores nephthys.

## Table

Throughput from `scratch/rebuild/evalrate.py` (ollama `/api/chat`, num_ctx 32768, warm).
First frame @5.5k = 5.5k / prompt tok/s on a single slot (the harness budget check halves
the rate for two shared slots, `harness/charging.py:57`). Canary verdicts are the fworker
attempt records in `foreman/_runs/wq<id>/attempt-<n>.json`.

| model | box | tools in ollama (`/api/chat` probe) | prompt tok/s | decode tok/s | first frame @5.5k | F1 / F2 / F3 | turns |
|---|---|---|---|---|---|---|---|
| llama3.1:8b (current) | hathor | structured | 14.6 | 10.1 | ~375 s | FALSE_COMPLETE (wq#2127, 0/114) | - |
| qwen3:14b (current) | nephthys | structured | 7.7 | 4.4 | ~715 s | KILLED first frame (wq#2127) | - |
| qwen2.5-coder:7b | hathor | declared, but text in `content` | 14.0 | 6.6 | 397 s (measured 5549 tok) | FALSE_COMPLETE x2 / FALSE_COMPLETE x2 / FALSE_COMPLETE x2 (wq#2862-2864) | 2 each |
| qwen2.5-coder:14b | nephthys | declared, but **0 structured tool_calls**, call returned as JSON text | 7.1 | 4.7 | 787 s (measured 5550 tok) | FALSE_COMPLETE x2 (wq#2865) / HANG first frame 2400 s, 2 slots shared (wq#2866) / not run (wq#2867) | 2 |
| gemma3:12b | hathor | **none** (`capabilities: [completion, vision]`) | 9.2 | 8.0 | ~600 s | not run: cannot drive a tool-calling CLI | - |
| qwen3:8b | nephthys | structured | 15.1 | 7.4 | ~365 s | attempt 1: TURNS / TURNS / queued (wq#2885-2887; attempt 2 pending at a2 end) | 10 (cap), 1069 s |
| devstral-small-2:24b (15.2 GB q4) | nephthys | structured | not measured | not measured | est. ~1060 s (8B->24B scaling of qwen3:8b) | not run (see below) | - |

## Findings

1. **The coder class fails before speed matters.** Both qwen2.5-coder sizes finish in exactly
   2 turns with a self-reported SUCCESS that fworker downgrades to FALSE_COMPLETE. The direct
   probe (`scripts/ollama_toolprobe.py <base> <model>`: one Bash tool, one "append a line" request)
   shows why: qwen2.5-coder:14b answers `{"name": "Bash", "arguments": {...}}` as plain
   `content` with `tool_calls: null`. The CLI sees a text answer, never runs the command,
   and the session ends. This is a known ollama issue with this template
   (github.com/NousResearch/hermes-agent/issues/5867). qwen3:8b, devstral-small-2:24b and
   llama3.1:8b return structured `tool_calls` for the same request.
2. **gemma3:12b is out by capability.** ollama declares no `tools` capability for it, so a
   tool-calling CLI cannot use it at all.
3. **Every box is CPU-only.** `/api/ps` reports `size_vram: 0` on both Macs for every model,
   so throughput is bounded by parameter count: ~15 prompt tok/s at 7-8B, ~7 at 14B.
   Two concurrent slots time-slice, which is how wq#2866 (coder14b) missed a 2400 s first
   frame while wq#2865 ran beside it.
4. **devstral-small-2:24b was not canaried.** It tool-calls correctly, but at 24B on a CPU-only
   box its prefill scales to ~5 tok/s. That puts a 5.5k first frame at ~1060 s on a single
   slot and ~2100 s under the shared-slot check, which is outside FOREMAN_FIRST_FRAME_S 1500.
   It did not fit this ticket's wall clock either.

## Incident: judge node re-tagged (a1)

a1 staged candidates by re-pointing the production tag (`ollama cp <candidate> llama3.1:8b`).
hathor is the dedicated judge node (wq#2796, pin `hathor-llama8b`), so the judge served
qwen2.5-coder:7b and then gemma3:12b from 03:18Z until a2 restored it at ~04:13Z
(`/api/copy bakeoff-orig/llama3.1:8b -> llama3.1:8b`; verified blob 667b0c19 =
`judge/JUDGE_MODEL_IDENTITY.json`, family llama, ctx 32768). wq#2821's blob guard now fails
bin/judge closed on such a swap. a2 ran every remaining candidate on nephthys only.
Logged as kb/ISSUE-LOG.md row 884.

## Restore state

- hathor: `llama3.1:8b` = original (blob 667b0c19), resident, ctx 32768. Candidate tags
  (qwen2.5-coder:7b, gemma3:12b, qwen3:8b) remain pulled but are not loaded.
- nephthys: `qwen3:14b` tag is still pointed at **qwen3:8b**, because the qwen3:8b canaries
  are running on it. The original is parked as `bakeoff-orig/qwen3:14b`; wq#2893 restores it
  unless it adopts qwen3:8b. The wq#2865-2867 coder14b canaries and a2's duplicate wq#2879
  are blocked with verdicts, so nothing else dispatches against the swapped tag.

## Routing action

None. Nothing in models.yaml, litellm or router chains changed. The lumen_state
`local-rung model_class` ingest failed twice with `sqlite3.OperationalError: database is
locked`, so it moves to wq#2893 together with the final qwen3:8b verdict.
