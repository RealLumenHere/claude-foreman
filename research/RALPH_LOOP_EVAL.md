# wq#1715 — ralph-loop vs Lumen's self-improvement loops

Date 2026-09-11 · worker `fworker-wq1715` · converts the cold Frank-gate wq#1077
into findings. Sources read: `upstream/main:ralph-loop/*` in `/home/lumen/pstack`
(MIT, cursor/plugins), `/home/lumen/autonomy/DGM_DESIGN.md`,
`/home/lumen/autonomy/DGM_V2_ROTATION_DESIGN.md`, `agent5/dgm_v2.py`,
`agent5/.dgm_history.jsonl`, `foreman/harness/fworker.py`,
`foreman/dispatcher/{runaway,spawn_loop_guard}.py`,
`ops/wq1643-split-watch-retire-agent5/RUNBOOK.md`.

---

## 1. Recommendation

**Do not port ralph-loop.** The fleet already runs its loop, with a mutating
prompt and four independent breakers where ralph has one honour-system
instruction and an optional integer. Porting it would add a second, weaker
iterate-until-done path beside the audited one.

| Question the ticket asked | Answer |
|---|---|
| What is it? | A fixed prompt replayed into the same session after every turn until the model declares itself done. §2 |
| Is it safe? | Not at the fleet's grade. Self-declared termination, `max_iterations` defaults to unlimited, no sandbox, no reviewer, no cost ceiling. §4 |
| Would it improve Lumen's cycle? | No. It is a strictly weaker version of `fworker`'s attempt loop. §3 |
| Anything worth taking? | Nothing buildable — its one good idea (machine-checkable completion criteria in the prompt) is already the acceptance gate. §5 |

The effort this ticket would have consumed is better spent on two real defects
found in the DGM loop while comparing against it (§6): the rotation wheel was
never implemented, and the config DGM optimizes no longer has a live consumer.
Both are filed.

---

## 2. What ralph-loop is

Geoffrey Huntley's "Ralph Wiggum" technique, packaged as a three-skill Cursor
plugin. Mechanism, from the vendored source:

- `skills/ralph-loop/SKILL.md` writes `.cursor/ralph/scratchpad.md` — YAML
  frontmatter (`iteration`, `max_iterations`, `completion_promise`) over the
  user's prompt body.
- `hooks/capture-response.sh` watches each response for
  `<promise>TEXT</promise>`; an exact match against `completion_promise`
  touches a `done` flag.
- `hooks/stop-hook.sh` fires at turn end. Absent the done flag and under the
  iteration cap, it increments `iteration` and returns the *unchanged* prompt
  body as `followup_message`. Otherwise it deletes the state file and lets the
  session end.

The prompt never changes; the working tree does. The agent's memory between
iterations is its own diff and git history. `cancel-ralph` removes the state
file.

Two properties matter for the comparison. **Termination is self-declared** —
the only thing standing between a bored model and `<promise>COMPLETE</promise>`
is the SKILL.md line "Do not output false promises to escape the loop."
And **the cap is opt-in**: `max_iterations` defaults to `0`, which
`stop-hook.sh` treats as unlimited; the README's advice is "always pass
`--max-iterations`", which is guidance, not a guardrail.

---

## 3. Lumen already runs this loop, better

`fworker` is a ralph loop at the queue layer. A worker is spawned headless on
one ticket; if the attempt fails, `write_prior_attempt_notes()`
(`harness/fworker.py:677`) writes the failure classification, a mechanical tail
of the last 20 transcript events, and `git diff --stat` of the dead worktree —
and the next attempt's prompt carries them. This document was produced by
attempt 2, reading attempt 1's `PERMISSION_LOOP` classification: attempt 1 was
loop-broken at 14 turns — `_runs/wq1715/attempt-1.json`, `loop_reason: same
command class failing 3x with no intervening successful change: bash:find
lumen`. A ralph loop replaying an unchanged prompt has nothing that detects
that shape, and would have deleted its state file with the session.

| | ralph-loop | fworker attempt loop |
|---|---|---|
| Prompt across iterations | identical | **mutates** — prior-attempt classification + event tail + diffstat |
| Context across iterations | accumulates in one session | fresh per attempt; state handed over as notes |
| Termination | model emits `<promise>` | `wq done` + independent critic pass sets `review_verdict`; a reject reopens (`reopened_count`) |
| Hard cap | `max_iterations`, default unlimited | `RUNAWAY_MAX_ATTEMPTS=5` (`runaway.py:35`) |
| Restart-storm cap | none | `SPAWN_LOOP_MAX=3` per 3 h (`spawn_loop_guard.py:43-44`) |
| Defect quarantine | none | harness-defect hold on classified defects (wq#1415) |
| Time bound | none | lease TTL per ticket |
| Failure attribution | none | `harness/classifier.py` — `PERMISSION_LOOP`, `TURNS`, `BUDGET`, `OOM`, … |

The one structural difference that favours ralph is context continuity: it
keeps the session, Lumen respawns cold. That is a deliberate trade
(`research/PREFIX_STABILITY.md`) — a cold attempt re-reads files but starts on
a cached, stable prefix and cannot inherit the confusion that killed the last
one. The prior-attempt notes are the chosen middle: carry the *evidence*
forward, not the context.

**DGM is not ralph's counterpart.** It sits one layer up and mutates
configuration, not code: propose → sandbox → paired-bootstrap score → gate →
human promote (`DGM_DESIGN.md`, `DGM_V2_ROTATION_DESIGN.md` §3-4). Ralph has no
scoring, no baseline, no promotion state machine. Comparing them is a category
error; the loop ralph competes with is `fworker`'s.

---

## 4. Safety

Against the Tier-0 instincts and the house deny-list:

- **Unbounded by default.** `max_iterations: 0` is unlimited. An unattended
  loop with no cost ceiling on a metered model is the exact shape
  `spawn_loop_guard.py` exists to stop — that guard was written on 2026-09-10
  (ISSUE-LOG #283) because a ticket respawned invisibly.
- **Self-graded exit.** The completion promise is checked by string equality
  against text the same model wrote. Lumen's equivalent — `wq done --notes` —
  is checked by a separate critic that can reject and reopen. Handing the exit
  condition to the agent under test removes the only adversarial check in the
  pipeline.
- **No sandbox.** Ralph iterates directly on the working tree. Lumen's
  self-modifying loop (DGM) is sandbox-first and human-gated precisely because
  unverified self-modification must never touch prod.
- **Blast radius rises with iteration count.** Each iteration is a fresh chance
  to hit a deny-listed action; the plugin has no per-iteration gate, so the
  only thing bounding harm is the PreToolUse hook — which is the fleet's
  guardrail, not ralph's.
- **Not portable as-is.** The plugin is Cursor-bound: `CURSOR_PROJECT_DIR`,
  `.cursor/ralph/`, and the `afterAgentResponse`/`stop` hook API. Adopting it
  means rewriting both hooks onto Claude Code's `Stop` hook — building a new
  unbounded loop, not porting a proven one.

---

## 5. What to borrow

One idea, already held: ralph's README is right that the technique lives or
dies on *machine-checkable* completion criteria ("tests passing", "coverage >
80%") rather than "make it good". Lumen encodes that as ticket acceptance
criteria and the done-means-demonstrated rule. Nothing to build.

Explicitly **not** worth borrowing: the `<promise>` tag (self-graded exit), the
unchanged-prompt replay (Lumen's mutating notes strictly dominate it), and
in-session iteration (trades prefix stability for context continuity — the
opposite of the call made in `PREFIX_STABILITY.md`).

---

## 6. Findings on the DGM loop (the comparison's byproduct)

**F1 — the rotation wheel was never implemented.**
`DGM_V2_ROTATION_DESIGN.md` §2 specifies six gene classes on a weekly wheel
(evaluator thresholds, retry budget, MODEL_CHAIN order, generator/planner/
evaluator prompts). `dgm_v2.py` hardcodes `target="generator_sys"` at both call
sites (`dgm_v2.py:92`, `dgm_v2.py:103`); `planner_sys` and `evaluator_sys`
exist in the `ROLE` map and are never selected. All 86 v2 cycles in
`.dgm_history.jsonl` target `generator_sys`. Five of six gene classes have
never been mutated in three months. Filed as **wq#1777**.

**F2 — the optimized config has no live consumer.**
A sweep of `/home/lumen/{autonomy,bin,agents,minerva,foreman}` finds
`config_registry.json` referenced by exactly two live programs, `agent5/dgm.py`
and `agent5/a5_common.py` (the rest are design docs and archives). The three
services that consumed it — `lumen-planner`, `lumen-generator`,
`lumen-evaluator` — are *not-found* on the host, confirmed by the
`systemctl list-unit-files` sweep in
`ops/wq1643-split-watch-retire-agent5/RUNBOOK.md` (2026-09-10). The daily
`lumen-dgm` run therefore optimizes a system prompt whose only reader is DGM's
own benchmark harness. Filed as **wq#1778**.

**F3 — yield, for context.** 91 cycles, 2026-06-16 → 2026-09-10: 83 `DISCARD`,
3 `PROMOTE` (08-18, 08-20, 08-24), 2 `pending`, 2 `discard`, 1
`discarded-by-human`. Nothing has promoted in the 18 days since. Read with F2,
the gate is working correctly on a benchmark that no longer measures anything
in production.

F2 is the finding that matters: the fleet's *real* self-improvement cycle is
the wq/fworker/critic loop, and it improves by shipping tickets. DGM is a
second cycle still burning a daily model call on a disconnected target. Fix or
retire it before adding any third loop — which is the strongest argument
against ralph.

---

## 7. Method note

The primary source (ghuntley.com/ralph) was not fetchable from a build worker —
`WebFetch` is not granted in `harness/worker-settings.json`. This evaluation
reads the vendored implementation at `upstream/main:ralph-loop/` instead, which
is the thing that would actually be ported. `/home/lumen/pstack` is outside the
worker's `--add-dir` bounds for `ls`/`find`/`grep`, but `git -C <path> show` is
allowed and was used to read the blobs (ISSUE-LOG #323).
