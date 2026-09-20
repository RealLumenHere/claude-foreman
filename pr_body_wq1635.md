## What

At attempt end, fworker now commits a deterministic digest of the per-run
`episode.jsonl` to archival_notes, and the never-built wq#1183 distiller
(`memory/distill.py` + `recall.py`) is retired to `memory/attic/`.

## Why

MINERVA_PIPELINE_MAP s1.17/s5#8: fworker writes `_runs/wq<id>/episode.jsonl`
per attempt (348 files, 26 in the last day), but the pattern-C design paired
it with `memory/distill.py` into `/home/lumen/fleet-memory/facts` — a store
that does not exist, run by no unit. `{{DISTILLED_MEMORY}}` was therefore
always empty and worker lessons reached no store any session reads.

## How

- `commit_episode_to_archive()` (harness/fworker.py:854), called right after
  the `attempt_end` episode event (harness/fworker.py:2675): commits via
  minerva's `lumen_episode.py commit` — memoryd `/commit` first, so trust
  derives from the lane claim in `$WQ_AGENT` (set by fworker at startup),
  audited direct write as fallback. Best-effort, fail-open like every other
  memory call on the worker's critical path.
- `memory/episodic.py digest_events()`: summary/changed/failed/why lifted
  mechanically from the `attempt_end` events — classification, killed_reason,
  PR URLs regexed out of result_text (Gitea `/pulls/N` and GitHub `/pull/N`),
  the worker's own final text as "why". No LLM. Items are scrubbed of `,`/`;`
  because the commit CLI re-splits list args on those.
- `memory/episodic.py sweep_episodes()`: `episode.jsonl` stays as raw
  evidence with a 30-day sweep, run best-effort after each commit.
- `distill.py`, `recall.py` + their tests → `memory/attic/` (superseded;
  `attic/README.md` explains). The always-empty distilled-fact recall source
  is removed from `distilled_memory_block`; the wq#1636 live-state source is
  unchanged. `memory/README.md` rewritten to match.

## Testing

- `cd memory && python3 -m pytest tests/ -q` → 20 passed (7 new digest/
  commit/sweep tests; commit subprocess monkeypatched, zero network).
- `harness/tests/test_evidence_gate.py` → 50 passed with clean env (the
  `distilled_memory_block` monkeypatch sites still work; failures under
  inherited `FOREMAN_*` env are pre-existing leakage, reproduced on primary).
- Live: committed the real wq2170 episode; `lumen_episode.py recall` returns
  it as top hit (0.76, archival/code-ship). The first-cut GitHub-only PR
  regex missed the Gitea URL — caught by this live check, fixed, covered by
  a unit test.

## Risk / rollback

Additive and fail-open: the commit + sweep run inside the existing
try/except pattern, so a broken minerva/memoryd degrades to a log line,
never a failed attempt. Rollback is reverting this commit; episode.jsonl
writing is unchanged, so no data is lost in the window.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
