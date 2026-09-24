# PILOT 784aecfd5aab — Add local LSP layer for code context retrieval

Experiment card. Generated 2026-09-23T09:11:13+00:00 by `minerva_defer_review` (wq#1559).
This card is the spec: a runner should be able to execute the pilot from it without
asking a question. Edit it before the run if it is wrong; never edit it during.

| | |
|---|---|
| Proposal | `784aecfd5aab` |
| Deferred | 2026-08-17 |
| Impact / effort / risk | M / L / M |
| Node | hathor |
| Wall-clock budget | 90 min |
| Token budget | 150000 |
| Report | `/home/lumen/foreman/pilots/784aecfd5aab/REPORT.md` |

## Why this is a pilot and not a research prompt

The paper provides a measurement methodology but not a concrete tool; applicability to Lumen's coding workflows is uncertain. Need to assess if Lumen's coding agents would benefit and if LSP integration is feasible on current hardware.

The question above is empirical: its answer is a number that does not exist yet.
Desk research can only report what someone else measured on other hardware.

## Hypothesis

(Filled in by the runner (wq#2438) BEFORE any scored call; the card was generated blank.)

Serving code-navigation answers from a local pyright language server needs at least 50% fewer context tokens than `grep -n -C3` for the same question, with at least 95% of the true reference sites recovered.

## Primary metric

Median tokens per question (tiktoken cl100k, a proxy tokenizer) for the LSP arm divided by the median for the grep arm, over 30 questions. Secondary, gating: reference recall of the LSP arm against an AST ground truth.
Verdict rule, fixed now: ADOPT if ratio <= 0.50 and recall >= 0.95. WATCH if ratio <= 0.80 but ADOPT fails. SKIP if ratio > 0.80.

## Fixed sample

Snapshot of the top-level `*.py` files in `/home/lumen/minerva` excluding `.bak` and test files, copied to `/tmp/orch-burn-B-3f4634/lsp/corpus` (mode 700): 64 files, 865,548 bytes; the harness prints the sha256 of the sorted per-file digests.
Questions: top-level functions defined exactly once in the corpus with at least 2 call sites in other statements. Ordered by sha256 of the name, first 30 taken. Each question is "where is X defined, what is its signature, who calls it", asked from the first call site.

## Baseline (measure FIRST)

Two baseline arms, computed before the LSP arm runs:

```
A  whole files: the file defining X plus every file containing a call site (raw text)
B  grep -n -w -C3 X over the corpus (what a competent agent does today)
```

B is the comparison that decides the verdict. A is reported for scale.

## Treatment

`pyright-langserver --stdio` 1.1.414, installed with npm into `/tmp/orch-burn-B-3f4634/lsp/npm` only (nothing system-wide). A 100-line JSON-RPC client asks `textDocument/definition` and `hover` at the first call site, and `textDocument/references` at the definition. The context an agent would receive is rendered as: hover text, `file:line` of the definition, and one `file:line: source line` entry per reference.
Ground truth for references is the set of `ast.Name` and `ast.Attribute` loads of the function name in the corpus (names defined once, so collisions do not inflate it). No service is wired.

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

Lumen currently has no local Language Server Protocol (LSP) integration; code context is retrieved via general-purpose LLM prompts without structured code intelligence.

## Proposal — proposed change

Integrate a local LSP server (e.g., pyright for Python) and a small service that queries it for symbols, definitions, and references to provide code context to coding agents, reducing prompt token usage.

---

The report at `/home/lumen/foreman/pilots/784aecfd5aab/REPORT.md` MUST end with, on its own line:

`RECOMMENDATION: ADOPT` (or `WATCH`, or `SKIP`)

followed by the measured numbers. A report without that line is re-run once and then
retired unjudged — the line is not optional.
