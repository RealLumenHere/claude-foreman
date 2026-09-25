## What

Guard the novel-design archetype signal `\bdesign( doc)?\b` so it no longer
fires on design-doc REFERENCES: a match that is part of a filename or path
component (`DESIGN.md`, `docs/design/...`) or takes a path argument
(`design[ doc] /path/...`) no longer counts (orchestrator/router.py:640-651).

## Why

Nine MCP-HA provisioning tickets whose detail opened
`Design /home/.../MCP_CONTROL_PLANE_HA_DESIGN.md §3.2` all classified
novel-design and routed to claude-fable-5@high, needing a manual `--archetype`
workaround. CLASS: keyword archetype classifiers fire on references, not intent.

## How

Reference-shape lookaround guards on the rubric pattern, not the ticket's
title-only option: the wq#1669 regression test
(`test_wq1669_analysis_ticket_no_longer_routes_to_mechanical_edit`) pins a
genuine design ticket whose only novel-design evidence is detail prose
("Design the replacement pacing curve"). Gating the signal on title would send
that class back to mechanical-edit. The guard fixes the observed reference
class while keeping wq#1669 green; prose intent still counts from title or
detail.

## Testing

New tests at orchestrator/tests/test_router.py:888-936:
- wq#2607 verbatim detail as the negative case (ticket AC)
- filename / path-component titles are not design signals
- prose intent from title or detail still classifies novel-design

Full router suite: 93 passed; the 5 auto-lane `repo_for_ticket` failures
pre-exist identically on the untouched main checkout (environment-dependent).
Live replay: `router.py classify 2607` now returns mechanical-edit (fleet
no-signal lane bias); 2605/2606/2608 likewise no longer novel-design.

## Risk / rollback

Classifier-only change, one regex; dispatch machinery untouched. A genuine
design ticket phrased ONLY as "design <path>" with no other design vocabulary
would fall back to lane bias / standard-engineering — acceptable, since that
phrasing is the citation shape by definition. Rollback: revert the single
rubric entry; tests localize the behavior.

Refs: wq#2615

🤖 Generated with [Claude Code](https://claude.com/claude-code)
