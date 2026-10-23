# Excerpt of /home/lumen/ORCHESTRATOR-HANDOFF.md as of 2026-10-22 (wq#4482), lines 228-233 and 287-294.
  /home/lumen/ORCHESTRATOR-HANDOFF.md — THIS document
  /home/lumen/AGENT-FILE-INDEX.md — Index of every doc that matters
  /home/lumen/scratch/subtraction/SESSION-BRIEFING.md — Session updates
  /home/lumen/claude_memory.md — Auto-generated from DB, DO NOT hand-edit
  /home/lumen/HANDOFF.md — Session handoff doc
  /home/lumen/minerva/ — Memory system, context builder

Episode spool (/home/lumen/.episode_spool/): a commit memoryd refuses (agent holds no live claim) is spooled, and
lumen_episode_spool_replay.py retries every 15 min. After 96 attempts or 14 days it moves to expired/. The note itself
always landed locally in archival first, so nothing is lost. Replay is refused BY DESIGN for dead sessions (identity gate I-7):
do not force it, and do not re-post another session's episode under your own id.

Rules: Do NOT add Anthropic memory slots for fleet facts. claude_memory.md is auto-generated.
After state changes run state-regen.
