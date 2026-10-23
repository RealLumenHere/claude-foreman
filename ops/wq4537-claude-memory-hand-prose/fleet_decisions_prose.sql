-- wq#4537 PROPOSAL ONLY (instinct tier-0 #7: nothing auto-edits claude_memory.md content).
-- fleet_decisions renders into claude_memory.md "## STANDING RULES" as
--   "- Prose/procedural detail -> this file. Update BOTH on any fleet fact change."
-- (seen live in minerva/context/sections/002_standing-rules.md:47). That row is the
-- fourth place telling agents to write prose where the regen wipes it. Review, then run:
--   bash /home/lumen/minerva/memory/psqlfm.sh -f <this file>
BEGIN;
SELECT id, scope, decision FROM fleet_decisions
 WHERE status = 'active' AND decision LIKE 'Prose/procedural detail -> this file.%';
UPDATE fleet_decisions
   SET decision = replace(decision,
         'Prose/procedural detail -> this file.',
         'Prose/procedural detail -> ONLY the hand-prose region at the bottom of this file; everything above it is regenerated every boot (wq#4537).')
 WHERE status = 'active' AND decision LIKE 'Prose/procedural detail -> this file.%';
-- Expect UPDATE 1. Then log it and regenerate:
--   fm_db.log_event(conn, '<agent>', 'update', 'fleet_decisions', <id>, None, None, 'wq#4537 hand-prose region')
--   python3 /home/lumen/minerva/memory/context_builder.py --out /home/lumen/claude_memory.md
COMMIT;
