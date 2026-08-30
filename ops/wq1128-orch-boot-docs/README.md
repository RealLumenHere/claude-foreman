# wq#1128 — tell the orchestrator to register and take a lane-set lease

Follow-up to wq#1116 (`ops/wq1116-orch-collision-guard/`), which built `wq orch --acquire/--release`
— one registered orchestrator per lane-set, leased and visible — but left the mechanism unused: no
boot path told an orchestrator to register or acquire it before dispatching. That is exactly what
let 2026-08-30's two-Commander collision happen: both sessions were unregistered, so wq#502's
holder checks, wq#530's staleness rules and wq#594's concurrency report all had nothing to key on.

## What changed

`/home/lumen/foreman/FOREMAN_DISPATCH.md` — a new section right after the file header, before the
per-ticket dispatch table, since it has to run before the very first ticket is claimed or worker
spawned: `wq whoami --register orch-<slug>`, export the `WQ_AGENT` it prints, `wq orch --acquire
<lanes>`, and `wq orch --release` on the way out. Edited directly (it is gitignored in this repo
and lives in the primary tree at `/home/lumen/foreman/`), so there is no diff to review here —
read the live file.

## What could not change from here

`lumen-session-start` is a Claude Code user skill at
`/home/lumen/.claude/skills/lumen-session-start/SKILL.md`. `guard.sh` denies both Read and Edit on
any path under `/home/lumen/.claude/skills/` from a build-worker sandbox — confirmed by testing,
not assumed: the same Read call that worked against `/home/lumen/foreman/FOREMAN_DISPATCH.md`
returned `BLOCKED — denied path` for `lumen-session-start/SKILL.md` and for a second skill
(`lumen-fleet-provisioning/SKILL.md`), so this is a blanket rule on the directory, not a fluke on
one file. Without being able to read the current file, no precise diff was possible — only a
standalone insertion, drafted in `lumen-session-start-snippet.md` in this directory, worded to
match the FOREMAN_DISPATCH.md section so both boot paths agree.

Filed as **wq#1137** (frank_gated — needs a session with `.claude/skills` write access, i.e.
interactive/Frank, not a decision to make) rather than left as a silent gap in this ticket's
acceptance: "queue it as a physical task, never as a gate that blocks the rest of the work."

## What this does not close

Both boot-path fixes tell an orchestrator to register and lease; neither can make it do so. A
session that skips the documented boot step is back to wq#1116's exposure. wq#1127
(`/home/lumen/minerva/context/claude_context.sh --core` refuses an anonymous boot instead of just
documenting the ask) closed separately and covers that harder edge — it landed the enforcement,
this ticket only landed the instructions.

## Verifying

Read `/home/lumen/foreman/FOREMAN_DISPATCH.md` directly — the new section is right after the file
header. `wq show 1137` for the residual's status.
