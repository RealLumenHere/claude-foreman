Target: /home/lumen/instructions/playbooks/session-start.md — append immediately after the
existing "## PARALLEL-SESSION COORDINATION" section (ends at its line 89 in the copy read
2026-09-11; verify current line number before inserting, the file may have moved since).
Source ticket: wq#1637, requirement 2 (procedure sections → playbooks). Extracted from
claude_memory.md:4451-4491 (ARTICLE-RESEARCH QUEUE, 2026-07-09) — the lane/dispatch specifics
in that section are already covered by agents/work-queue/ARTICLE_RESEARCH_DISPATCH.md
(confirmed present); this is only the generic `wq` verb reference that section names but
session-start.md doesn't yet spell out beyond claim/heartbeat/preflight.

---

## WQ CLI REFERENCE
```
wq add --lane <lane> --pri N --title "..." --detail "..."   # create a ticket (dedups on normalized title; --force to override)
wq list [lane]                                               # see the queue
wq show <id>                                                 # read a ticket
wq claim --lane <lane> [agent]                                # atomic claim; parallel-safe
wq edit <id> [--title T|--detail D|--pri N|--lane L|--status S]  # edit/merge a ticket
wq done <id> --notes "<PR url + one-line summary>"            # close with evidence
wq release <id>                                               # return a claimed ticket to open
wq block <id> "<reason>"                                      # park a ticket needing Frank
```
`user` lane is Frank-only — `wq claim` refuses it. See "PARALLEL-SESSION COORDINATION" above
for claim/heartbeat/preflight and slock.
