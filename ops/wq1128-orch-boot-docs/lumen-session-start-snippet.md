Paste this as an early step in the boot sequence of
`/home/lumen/.claude/skills/lumen-session-start/SKILL.md` — before the session claims or dispatches
anything, whenever the session is acting as an ORCHESTRATOR (spawning fworker/build tickets, not a
single-ticket build worker itself). Wording matches the FOREMAN_DISPATCH.md addition made for the
same ticket (wq#1128) so both boot paths agree.

---

## Register and take the lane-set lease before dispatching (wq#1128)

wq#1116: on 2026-08-30 01:22–01:29Z two claude.ai orchestrator sessions rode one Commander with
neither registered. Both fell back to `lumen:<pid>`, so every safety net that keys on a registered
identity — wq#502's holder checks, wq#530's staleness rules, wq#594's concurrency report — had
nothing to read, and the second session spawned workers into a batch the first was still verifying.
If this session is orchestrating (about to `wq claim` on behalf of workers, or spawn any
fworker/build ticket), do this first, before the first claim or spawn:

```
wq whoami --register orch-<slug>     # prints REGISTERED session + "export WQ_AGENT=<sid>"
export WQ_AGENT=<sid>                # paste the id it printed — do not skip this line
wq orch --acquire <lanes>            # the lane(s) you are about to dispatch into
```

If `--acquire` refuses, another orchestrator already holds one of those lanes — the refusal names
who and since when. Do not dispatch into that lane-set anyway; take a disjoint set, wait for the
lease to lapse, or (only if you know that session is dead) `wq retire <that identity>`.

On the way out, always: `wq orch --release`. `wq orch` with no flags shows who currently holds
what, and is safe to run any time as a sanity check.

---

Why this can't be applied by the build worker that wrote it: `/home/lumen/.claude/skills/` is
explicitly denied to build-worker sandboxes (guard.sh blocks both Read and Edit on any path under
it), so this file could not be read to produce a precise diff, only drafted as a standalone
insertion. See ops/wq1128-orch-boot-docs/README.md.
