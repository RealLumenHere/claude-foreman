# Fleet agent memory — episodic log + archival commit (wq#1183, rewired wq#1635)

Workers produce an append-only EPISODIC log as a side effect of running; at
attempt end fworker commits a **deterministic digest** (no LLM) of that log to
the durable archive every session already reads:

```
EPISODIC (raw, append-only, per-run)              ARCHIVAL (durable, queried)
RUNS_ROOT/wq<id>/episode.jsonl  --commit_to_archive()-->  minerva archival_notes
written by fworker at attempt end                 via lumen_episode.py commit
kept 30 days (sweep_episodes), raw evidence       (memoryd first: lane trust
                                                   from $WQ_AGENT; audited
                                                   direct write as fallback)
```

The original wq#1183 pattern-C design (distill.py LLM extraction into
`/home/lumen/fleet-memory/facts`, recall.py → `{{DISTILLED_MEMORY}}`) was
never built out — the fact store did not exist and no unit ran the distiller,
so the recall block was always empty. **Superseded by wq#1635**; the pieces
are preserved in `attic/` (see `attic/README.md`).

## Pieces

| file | role |
|---|---|
| `episodic.py` | append-only writer/reader for `RUNS_ROOT/wq<id>/episode.jsonl`; `digest_events()` + `commit_to_archive()` (episode → archival note); `sweep_episodes()` (30d retention) |
| `cmt_retrieval.py` | (unchanged) retrieval helpers |
| `attic/` | superseded wq#1183 distiller: `distill.py`, `recall.py`, their tests |

## Paths / env

- `FOREMAN_RUNS_ROOT` (default `/home/lumen/foreman/_runs`) — episode store.
- `LUMEN_EPISODE_BIN` (default `/home/lumen/minerva/lumen_episode.py`) — the
  archival commit CLI.

## Verify

```
python3 /home/lumen/minerva/lumen_episode.py recall "wq<id>"   # shows the note
/home/lumen/bin/handoff                                        # recent episodes
```

## Failure posture

Everything on the worker's critical path is fail-open: fworker wraps the
episode append and the archival commit in try/except, so a broken store can
degrade memory to "none" but can never fail an attempt.

Tests: `cd memory && python3 -m pytest tests/ -v` (zero quota, no network —
the commit subprocess is monkeypatched).
