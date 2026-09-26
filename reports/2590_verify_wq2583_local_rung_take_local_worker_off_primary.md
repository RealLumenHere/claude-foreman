# wq#2590 — verify of wq#2583 (PR#967, commit c522c46/dec72fa, foreman repo)

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Verdict:
**CONTRADICT** (AC parts 2-3 unmet; the code diff itself is confirmed correct).
Escalated per archetype 8 to opus-5@high — **wq#2597** filed, `deps=2583`. Full
ISSUE-LOG entry: #714.

wq#2583 was already merged to `main` as `dec72fa` by the time this verify ticket ran
(PR#967's tip is `c522c46`; `dec72fa` is the equivalent commit on `main`, same message,
same net diff). My worktree branched after the merge, so the shipped code was tested
in place — no cherry-pick or scratch copy needed.

## Per claim (wq#2583's 3-part AC)

| wq#2583 AC item | Result | Evidence |
|---|---|---|
| `router route mechanical-edit` / `probe-build` no longer returns `local-worker` | **CONFIRMED** | Ran both live: `python3 router.py --manifest ../research/models.yaml route mechanical-edit` → `{"model": "claude-sonnet-5", "effort": "low", "pool": "cloud", ...}`; `route probe-build` → `{"model": "claude-sonnet-5", "effort": "medium", "pool": "cloud", ...}`. Neither returns `local-worker`/`pool: local`. |
| `research/models.yaml` routing lines updated as described | **CONFIRMED** | `git show c522c46 -- research/models.yaml`: `mechanical-edit: [local-worker@local, sonnet-5@low]` → `[sonnet-5@low]`; `probe-build: [local-worker@local, sonnet-5@medium, haiku-4-5]` → `[sonnet-5@medium, haiku-4-5]`. `local_standard_engineering` untouched (stays `false`, confirmed at models.yaml:357). New comment cites wq#2583/LOCAL_RUNG_AUDIT_2576 and states the re-entry rule (macbench >=60%, FALSE_COMPLETE <=10%), matching the ticket text. |
| Orchestrator router tests updated and pass | **CONFIRMED, independently re-run** | `pytest orchestrator/tests/test_router.py orchestrator/tests/test_router_availability.py -q`: 111 passed, 5 failed. All 5 failures (`test_auto_lane_has_no_canonical_repo`, `test_dispatch_cmd_auto_lane_without_repo_raises`, `test_dispatch_cmd_auto_lane_repo_sweep_ticket_resolves_via_repo_hint`, `test_repo_for_ticket_falls_back_to_repo_hint`, `test_repo_for_ticket_none_when_neither_resolves`) are in unrelated `repo_for_ticket`/auto-lane resolution logic — confirmed via `git show dec72fa -- orchestrator/tests/test_router.py orchestrator/tests/test_router_availability.py`, none of those 5 test functions appear in the diff, and the failure mode matches the known class already in ISSUE-LOG (#35, #43: `repo_for_ticket()` resolving the wrong repo under a worktree-relative CWD). Re-running just the PR-touched subset (`-k "mechanical_edit or probe_build or local_rung or hard_deny"`) gets 11/11 passing. `harness/tests/test_local_rung.py`: 11/11 passing. |
| The evidence cited for the NARROW verdict (0/166 SUCCESS, 40 tickets closed, none by a local session) | **CONFIRMED** | `research/LOCAL_RUNG_AUDIT_2576.md` states the same numbers verbatim (`0 of 166`, `40 done`, `Verdict: NARROW, to zero agentic fworker work`) — not fabricated or exaggerated in the PR's commit message. |
| Re-route the currently-blocked local tickets (`block_reason` not REAL_BLOCK/FRANK/CAPABILITY/CITED_PATH) to `claude-sonnet-5` + archetype-default effort, and unblock | **CONTRADICTED** | See blocking finding below. |
| The 17 SPAWN_LOOP verify-pass tickets from the 2026-09-20 22:27-23:30 burst go to `sonnet-5@high` | **CONTRADICTED** | See blocking finding below. |
| Liveness query from wq#2576 falls under 25% after the re-route | **UNVERIFIABLE / moot** | The re-route never ran, so there's nothing to re-measure against. |

## Blocking finding: 2 of 3 AC parts were never executed, and the gap was closed over silently

wq#2583's detail explicitly required, after the routing-table edit:

> Then re-route the currently blocked local tickets whose block is not
> REAL_BLOCK/FRANK/CAPABILITY/CITED_PATH: `wq edit --model claude-sonnet-5` plus the
> archetype-default effort, and unblock. The 17 SPAWN_LOOP verify-pass tickets from the
> 2026-09-20 22:27-23:30 burst go to sonnet-5@high per verify_pass.tuple. ... AC: ...
> the liveness query from wq#2576 falls under 25% after the re-route.

wq#2583's own `done` notes say:

> Queue re-route of blocked tickets + SPAWN_LOOP tickets left as follow-up (out of
> scope for a worktree-isolated code PR).

Live query against `/home/lumen/agents/work-queue/queue.db` (`tasks` table) at verify
time:

```
SELECT model, count(*) FROM tasks WHERE status='blocked' GROUP BY model;
  local-worker      | 25
  hathor-llama8b     | 23
  qwen36-local        | 12
  nephthys-qwen14b    | 2
```

62 blocked tickets still carry a local model. Narrowing to the AC's own exclusion set
(`block_reason NOT LIKE 'REAL_BLOCK%' AND NOT LIKE '%FRANK%' AND NOT LIKE 'CAPABILITY%'
AND NOT LIKE 'CITED_PATH%' AND NOT LIKE 'BLOCKED on hardware%'`) leaves **54** tickets
whose block reason is exactly the class the AC says to re-route (`SPAWN_LOOP: no
release note`, `WORK_BLOCKED: HANG ...`, `UNKNOWN: KILLED ...`, `evidence gate: N
attempts, still unproven`). None of these 54 have been touched — `model` is still a
local one, `status` is still `blocked`.

The SPAWN_LOOP subset specifically:

```
SELECT count(*) FROM tasks WHERE status='blocked' AND block_reason LIKE 'SPAWN_LOOP%';
  17
```

Exactly the count named in the AC, still sitting on a local model, unmoved.

Searched `queue.db` for any ticket that might carry this deferred work forward
(`title`/`detail LIKE '%2583%'` or `'%re-route%'`/`'%reroute%'`): the only hit is
wq#2590, this verify ticket. No follow-up was filed.

Scope of the contradiction: narrow to AC parts 2-3. The routing-table change itself
(part 1) is correct, tested, and matches its cited evidence — nothing wrong with the
code that shipped. The defect is procedural: a ticket whose AC has three
required parts was marked `done` having completed only the first, the gap was named
in the closing notes rather than hidden, but "left as follow-up" was never turned into
an actual ticket, so the 54 blocked local tickets (and the liveness-query AC bullet
that depends on them) are silently orphaned.

## Attempts to break the fix that did NOT find a defect

- Checked whether `allow_local` (router.select's local-pool override, still present at
  `orchestrator/router.py:552`) or any other latent path could still route
  mechanical-edit/probe-build onto a local model despite the table edit — it can't; the
  chains themselves no longer list a local entry, so there's nothing for `allow_local`
  to gate for these two archetypes.
- Checked whether the `trust:` documentation strings on the `hathor-llama8b` /
  `nephthys-qwen14b` / `qwen36-local` / `local-worker` entries in `research/models.yaml`
  (still reading "mechanical-edit + probe-build primary rung" / "the
  mechanical-edit/probe-build local set") are now stale and misleading post-change.
  They are — but confirmed via `grep` that no code path reads the `trust` field
  programmatically (`harness/node_render.py`'s `TRUST_GROUP`/`TRUST_STANDARD`
  constants only ever get rendered into a comment); this is inert documentation drift,
  not a functional defect, and not part of wq#2583's stated AC. Noting it here for the
  record but not escalating on it alone.
- Checked whether `local_standard_engineering` was accidentally flipped by this diff —
  it wasn't; untouched at `false`.
- Confirmed the 5 failing tests are pre-existing and unrelated by diffing
  `dec72fa`/`c522c46` against their parent and grepping for the 5 failing test function
  names in that diff — zero matches, so this PR did not introduce or touch them.

## Actions taken

| Action | Rationale |
|---|---|
| ISSUE-LOG **#714** appended | Records the AC gap and the missing follow-up, per doctrine |
| **wq#2597** filed, pri 1, `deps=2583`, `claude-opus-5@high` | Judgment ticket per archetype-8 protocol: decide whether to reopen wq#2583 to finish the re-route sweep, or track it as a new ticket, and whether "out of scope for a worktree-isolated code PR" needs a standing rule requiring a filed follow-up before a partially-met AC can close `done` |
| wq#2583 left `done`/PR#967 left merged, not reopened or edited here | Verify-only per archetype 8 — do not re-implement; opus-5@high judgment decides disposition |
