# wq#2597 — judgment on wq#2590's CONTRADICTED verdict against wq#2583

Judge: claude-opus-5@high, fresh instance, worktree `wq2597-a1`.
Every number below was re-derived here against the live `queue.db` before deciding; wq#2590's
report was treated as a lead, not as evidence.

## Verdict

**wq#2590's CONTRADICTION is upheld. wq#2583 is NOT reopened — its unrun AC parts were
executed here, and the pattern that let them go unrun is now gated in code.**

Three findings, in order of weight.

### 1. The gap was real and exactly as reported

At the start of this run, against `/home/lumen/agents/work-queue/queue.db`:

| query | result |
|---|---|
| blocked rows still carrying a local model | 62 (local-worker 25, hathor-llama8b 23, qwen36-local 12, nephthys-qwen14b 2) |
| of those, block outside REAL_BLOCK/FRANK/CAPABILITY/CITED_PATH | 54 |
| SPAWN_LOOP subset | 17, byte-identical to the count wq#2583's own AC named |

wq#2583's AC part 2 ("re-route the currently blocked local tickets ... and unblock") and part 3
(wq#2576's liveness under 25%) were unsatisfied 5 days after the close. Part 1 (the
`research/models.yaml:330-331` routing change and its router tests) was correct — wq#2590
re-ran it, and nothing here disturbs it.

### 2. Reopening was the wrong instrument; the sweep is a tool, not a re-dispatch

`wq#2583`'s artifact — PR#967 — is merged and correct. Reopening a done ticket whose PR landed
gets the branch's prior work frozen as an ancestor of the rework diff base (ISSUE-LOG #307),
so the rework attempt has to invent a fresh commit anyway. The deferred work is also not the
same *kind* of work as the PR: it is 54 queue mutations, which no diff can express.

So the sweep landed as a reviewable artifact instead of a re-dispatch:
`harness/reroute_local_blocked.py`, dry-run by default, writing only through the `wq` CLI, with
`harness/tests/test_reroute_local_blocked.py` asserting the two ways a sweep like this goes
wrong (it unblocks a ticket whose block was never about the rung; it touches a block the AC
excluded). Applied result:

```
candidates=54 rerouted=54 unblocked=53 skipped=0
LIVENESS=0
```

* 54 rows re-pointed at the first **cloud** rung of their own archetype chain — mechanical-edit
  → `claude-sonnet-5@low`, probe-build → `@medium`, standard-engineering and the 17 verify-pass
  SPAWN_LOOP rows → `@high` (`research/models.yaml:330-348`). Two rows with no archetype got the
  model and no effort, so the router still derives it at dispatch.
* 53 unblocked. The one exception is **wq#1953** (`BLOCKED on a missing spec:
  pilots/1b7e443bb0fa/CARD.md`): re-routing cannot make a missing spec appear, so it was
  re-pointed and left blocked. Its model no longer names a retired rung, which is what the
  liveness query is actually asking.
* Stale claims from the local sessions that died holding them (`fworker-wq463`,
  `fworker-wq1819`, …) were released, or the next `wq claim --id` would refuse on the dead holder.

Post-sweep, independently re-queried:

* wq#2597's own liveness query: **0**.
* wq#2576's probe (blocked share of local-model rows created in 30d): **7/52 = 13.5%**, under
  its 25% closure bar. The 7 are the protected blocks the AC deliberately excluded
  (FRANK-gated ×2, CITED_PATH ×2, CAPABILITY_DENIED, a langfuse dependency, "blocked on
  hardware: maksoor unreachable").

### 3. "Out of scope for a worktree-isolated code PR" is false, and its honest form still needs a ticket

The ticket asked whether that phrase is an acceptable reason to defer an AC bullet without
filing the deferred work. Two separate answers:

**As a factual claim it is wrong.** This judgment ran from a worktree-isolated session and did
the entire queue sweep from inside it, through the same `wq` CLI every worker already has, and
shipped the reviewable part as a tool plus tests. A worktree PR cannot *contain* a queue
mutation, but it can carry the thing that performs one and the evidence that it ran. The
accurate sentence would have been "I am not doing the sweep in this run", which reads very
differently in a `done` closure.

**As a scoping decision it is legitimate; as a reason to close silently it is not.** Deciding
not to do part of an AC in one run is ordinary engineering. Dropping it is not, and the cost is
measurable: this gap consumed a verify-pass (wq#2590) and an opus judgment (wq#2597) to recover
work that one `wq add` would have kept. **Standing rule, effective now:**

> A closure whose notes defer work must name the ticket that carries the deferred work.

It is enforced mechanically, not by doctrine, because the doctrine version depends on a worker
remembering one `wq add` in its last 30 seconds — which is precisely what failed here.
`harness/deferral_gate.py`, wired into `harness/fworker.py`'s `wq_done()`, scans the closing
notes for deferral language and, when nothing else is cited, **files the follow-up itself**
(`deps=<parent>`, the deferring sentence quoted in the detail) and appends `wq#N` to the notes.

The direction — file, not refuse — is deliberate. A refusal at `wq_done` strands a
finished-as-scoped ticket under a worker that has already stopped, and it punishes the honest
note: the worker who writes the deferral down plainly takes a failed attempt while one who
writes nothing sails through. Auto-filing inverts that incentive, and the cost of a false
positive is one retirable row. When the filing itself fails, the notes carry
`DEFERRAL-UNFILED: <reason>` — a silent gate here would just re-create wq#2583.

## Spun off, not deferred silently

**wq#2616** — `wq edit --attempt`: 19 of the 54 swept rows carry `attempt>=3`, and the evidence
gate blocks on the row counter at `FOREMAN_EVIDENCE_MAX_ATTEMPTS` (`harness/fworker.py:3952-3960`),
so their first sonnet attempt is also their last. Those attempts were all spent on the rung
wq#2576 measured at 0/166 SUCCESS. `wq edit` has no `--attempt` flag and this sweep writes only
through the wq CLI — resetting the counter by hand would make it a second writer to a schema it
does not own — so the fix is a wq change with its own ticket.

## Notes

* Prior-art search was attempted per standing order and is unavailable: every SearXNG general
  engine is CAPTCHA'd/rate-limited and there is no Brave key (known, wq#2595). The question is
  fleet-internal policy, so nothing external was load-bearing here.
* Pre-existing, untouched by this branch: 5 `test_stop_hook_*` failures in
  `harness/tests/test_evidence_gate.py`. Reproduced identically in the main checkout
  (`/home/lumen/foreman`) at its own HEAD.
