# PILOT 6161e33a06f9 — Encode redundant context as images in prompt pipeline

Experiment card. Generated 2026-09-21T09:11:37+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `6161e33a06f9` |
| Deferred | 2026-08-13 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/6161e33a06f9/REPORT.md` |

## Why this is a pilot and not a research prompt

Uncertain benefit for local models; token savings unclear and risk of degrading prompt fidelity. Needs a small pilot to measure token reduction and quality impact.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2422); the card was generated blank. The single measurement below is the primary metric itself, taken before any fidelity arm.)

Rendering the static persona prompt as an image cuts its prompt tokens by at least 30% versus sending it as text.

## Primary metric

`prompt_tokens` for the same content: image arm (as reported by the gateway usage field) vs text arm (tiktoken cl100k). Unit: tokens. Win: image <= 0.70 x text.

## Fixed sample

The static prompt text of `/home/lumen/agent/persona_engine.py`: its 2 string literals longer than 200 chars, 2,593 chars total, joined with a blank line. Rendered once with PIL (default bitmap font, wrapped at 150 chars) to a 1400 x 680 PNG, 55 lines.

## Baseline (measure FIRST)

```
python3 -c "import tiktoken; ..."   # cl100k over the joined literals
text tokens: 615
```

## Treatment

One call to `lumen-vision` (`openrouter/qwen/qwen3-vl-32b-instruct`, the only vision rung in the gateway) with the PNG plus the 6-word instruction "Reply with the single word OK." at max_tokens 5. Nothing was wired into any service.

## Kill criteria

<!-- When to stop early WITHOUT asking permission. Stopping on a kill criterion is a
successful pilot whose answer is SKIP or WATCH, not a failed one. -->

- Wall-clock exceeds 90 min -> stop, report what was measured.
- Baseline does not reproduce -> stop, report the baseline defect. Do not proceed.
- Treatment incomplete at budget -> report partial numbers, RECOMMENDATION: WATCH.

## Data handling

`/home/lumen/minerva` is not a broker read root (credentials are mirrored into its
context sections) and `minerva.db` is never copied to a pilot node. A pilot that
needs fleet data takes a purpose-built extract of only the columns it needs, written
on Lumen, and the extract is listed here.

## Scope limit (standing, not per-pilot)

A pilot NEVER wires anything into prod. An ADOPT verdict feeds minerva-factory PLAN
like any other accept and is built through the normal path, with its own review.

## Proposal — current state

Lumen's prompt engineering pipeline (lumen-prompt-engine, lumen-persona-engine) currently sends all context as text tokens through LiteLLM, with no image-based context encoding.

## Proposal — proposed change

Add a new service/script that converts redundant context (e.g., static system prompts, persona files) into images (e.g., via PIL) and injects them as image inputs in prompts, reducing token usage. This would require modifying the prompt assembly in lumen-prompt-engine or lumen-persona-engine to support image attachments.

---

The report at `/home/lumen/foreman/pilots/6161e33a06f9/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
