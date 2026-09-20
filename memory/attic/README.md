# attic — superseded by wq#1635

The wq#1183 pattern-C distiller was designed but never built out: `distill.py`
was meant to run over `RUNS_ROOT/wq<id>/episode.jsonl` and emit facts into
`/home/lumen/fleet-memory/facts`, which `recall.py` would inject as
`{{DISTILLED_MEMORY}}`. The fact store was never created and no unit ever ran
the distiller, so the recall block was always empty and worker lessons reached
no store any session reads.

wq#1635 replaced the whole extraction step: `harness/fworker.py` now commits a
deterministic (no-LLM) digest of the episode to archival_notes at attempt end
via `memory/episodic.py commit_to_archive()` → minerva's `lumen_episode.py`
(memoryd first, lane trust from `$WQ_AGENT`). `episode.jsonl` remains as raw
evidence with a 30-day sweep.

Kept here (not deleted) so the extraction contract and its tests stay
readable; nothing imports these modules.
