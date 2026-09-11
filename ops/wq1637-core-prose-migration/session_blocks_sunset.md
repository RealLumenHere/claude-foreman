# Requirement 4 — SESSION `<date>` blocks stop being written

## What's being retired

The `## SESSION <date> (...)` convention in claude_memory.md, strict all-caps match. 5 found,
2026-06-26 through 2026-09-08 (see README.md §4 for exact lines). 4 of the 5 are dated the
same day, from 4 different concurrently-running orchestrator sessions — evidence the
convention was actively in use, unowned, and about to keep growing linearly with concurrent
session count rather than with actual event count.

## Where the three streams go instead

| was in a SESSION block | now goes to | mechanism |
|---|---|---|
| in-flight state (open items, blockers, "next session should...") | merged HANDOFF.md | wq#1630: `session-handoff` writes into a generated region of HANDOFF.md; `claude_context.sh --core` re-renders it at read time |
| lessons / what-worked / what-failed / root causes | `archival_notes` (minerva.db) | `lumen_episode.py commit --summary ... --changed ... --worked ... --failed ... --why ... --ref "session:<date>"` — this is the `episodic-recall-guard` tier-1 instinct (§1 of the main migration), made mandatory-only here |
| fleet facts that changed | `state_facts` (minerva.db) | `lumen_state.py ingest <subject> <predicate> <value> --valid-from <date> --source <ref>` — the `bi-temporal-state-query` tier-1 instinct |

None of these three mechanisms are new inventions of this ticket — all three already exist and
are already instinct/playbook material (§1 candidates `bi-temporal-state-query` and
`episodic-recall-guard`). What was missing was an explicit statement that the SESSION-block
convention is *retired*, not just "superseded in spirit." That statement is what
`episodic-recall-guard`'s text (instincts_candidates.yaml) adds: "claude_memory.md 'SESSION
<date>' blocks are RETIRED (wq#1637)."

## Why a rule alone, not a mechanical enforcement

Unlike the wq-ticket lease/slock enforcement (wq#317), there's no chokepoint a session must
pass through to append prose to claude_memory.md — any orchestrator session can always just
write to the file. A hard technical gate (e.g. a pre-commit-style check on claude_memory.md
edits) is out of this ticket's scope and possibly out of scope for prose entirely (the file
isn't in a git repo — there's no hook surface at all). The instinct text is the only lever
available at this layer; recommend a future `lumen_dream` growth-metric check (ticket already
names "lumen_dream growth metrics" as part of verify) specifically watch for new `## SESSION`
headings appearing and flag it as a regression, since that's a mechanical signal this ticket
can point at even without a write-time gate.

## Out of scope, flagged for a follow-up ticket

A much larger population of informally-dated headings — `Session end/update/continued`,
`FABLE BATCH`, `SHIFT REPORT` — was NOT touched by this migration. Rough estimate (subagent
pass, not independently verified): several hundred lines scattered roughly lines 1136-4600.
The ticket's own example ("SESSION 2026-09-08") only names the strict all-caps convention, and
without a read-through of every one of those entries this session can't tell which are truly
dead log noise vs. still-load-bearing project narrative (the Watch8/FingerFind and
lumen-pr-land-ff blocks flagged in `diffs/02-delete-session-blocks.diff` blocks 4-5 are exactly
this kind of judgment call, at smaller scale, inside even the strict-match set). Recommend a
dedicated read-through ticket rather than folding it into this diff.
