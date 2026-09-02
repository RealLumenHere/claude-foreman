# Fleet agent memory — episodic vs distilled (wq#1183)

Pattern C of `kb/fleet-architecture-ideas.md` §1 (eliza evaluators / PraisonAI
memory tiers), scoped to the report's §3 "day 5" first cut. Fleet agent memory
splits into two stores with an **explicit extraction step** between them:

```
EPISODIC (raw, append-only, per-run)          DISTILLED (durable facts)
RUNS_ROOT/wq<id>/episode.jsonl    --distill.py-->   FLEET_MEMORY_ROOT/facts/*.md
written by fworker at attempt end             git/markdown (+ lumen_state proposals)
                                                        |
                                              recall.py --> {{DISTILLED_MEMORY}}
                                              in the next worker's prompt prelude
```

The core discipline (report §1.C): **agents never write durable memory
directly.** Workers only produce episodes as a side effect of running; a
separate distiller pass turns episodes into facts. That gives one throttle
point for quality, provenance on every fact (`source_ticket` frontmatter), and
re-runnable distillation — keep the episode, improve the prompt, distill again.

## Pieces

| file | role |
|---|---|
| `episodic.py` | append-only writer/reader for `RUNS_ROOT/wq<id>/episode.jsonl` |
| `distill.py` | the extraction step: episode → ≤5 validated, secret-scrubbed facts → markdown note (git commit best-effort) + proposed `lumen_state` ingests |
| `recall.py` | top-k newest deduped facts → prompt block for `{{DISTILLED_MEMORY}}` in `harness/prompts/worker.md` |

`distill.py` is shape-matched to the KB distiller
(`projects/lumen-kb-mcp/distill.py`) and the wq#1176 LPP persona extractor: one
gateway chat call, strict output contract, durable store with provenance.

## Paths / env

- `FOREMAN_RUNS_ROOT` (default `/home/lumen/foreman/_runs`) — episode source.
- `FLEET_MEMORY_ROOT` (default `/home/lumen/fleet-memory`) — distilled store.
  `git init` it once; distill.py commits only if `.git` exists.
- `FLEET_MEMORY_MODEL` (default `qwen36-local`) — extraction model (local rung;
  distillation never spends Anthropic quota).
- `LUMEN_STATE_CLI` (default `/home/lumen/minerva/lumen_state.py`).

## Usage

```
python3 memory/distill.py --ticket 1183            # extract; print note path +
                                                   # proposed lumen_state ingests
python3 memory/distill.py --ticket 1183 --ingest   # also run the ingests
```

Ingests are **proposal-only by default** — the same posture as instinct 6
(memory consolidation is proposal-only): printing the exact
`lumen_state.py ingest` argv is the low-risk default; `--ingest` is the
explicit opt-in.

## Deliberately NOT in this cut

- **No vector DB / embeddings** (report §2: "measure before you embed") —
  recall is recency + scope filter over markdown.
- **No auto-enqueue of a distill wq item per completed task** yet — run the CLI
  manually or from cron/dispatcher; wire the enqueue once a week of episodes
  shows the fact quality is worth the calls.
- **No cutover of existing memory** — claude_memory.md, project STATUS.md,
  lumen_state all keep working exactly as before; this adds a parallel path.

## Failure posture

Everything on the worker's critical path is fail-open: fworker wraps the
episode append and the recall injection in try/except, so a broken store can
degrade memory to "none" but can never fail an attempt.

Tests: `cd memory && python3 -m pytest tests/ -v` (zero quota — `chat` is
monkeypatched).
