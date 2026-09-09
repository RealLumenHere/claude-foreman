# DEFER_PILOT_LANE — an outlet for defers that need an experiment, not a search

wq#1559 · design doc · 2026-09-08 PDT · agent work-1559-ae5413

Status: DESIGN (written before build, per instinct tier-1 DESIGN BEFORE BUILD).
Scope: minerva defer pipeline + foreman pilot lane + the first pilot run.

## 1. Root cause

`minerva_defer_review.py` has exactly three outcomes: RESEARCH, REAP, RETIRE. All
three are desk work. A defer whose reason is "we do not know until we measure it"
has no outlet that measures anything, so it takes one of two bad paths:

1. it matches `INFO_GAP`, becomes a *desk research* prompt, and comes back with a
   literature answer to an empirical question; or
2. it matches neither regex, ages past `STALE_DAYS`, and retires unjudged.

Two defects, both class-level:

**D1 — no experimental outlet.** 92 of 316 open defers carry pilot/benchmark/A-B/
measure-shaped reasons. The pipeline can read a paper about them; it cannot run
them. Meanwhile hathor (24 threads, 60G, load 0.00, up 8d) is idle. The gap is not
capacity, it is that "run this on a box" was never a state the pipeline could emit.

**D2 — form failure is scored as a verdict.** `_verdict_from()` returns
`rejected_aged` when the note has no `RECOMMENDATION:` line. That conflates *the
research says no* with *the writer did not follow the template*. 19 of 72 researched
defers (26%) died that way. The proposal was never judged; a formatting miss killed
it. Worse, nothing upstream enforces the format, so the failure recurs by design.

Both are the same shape as the house pathology in claude_memory.md ("a fail-open
path makes a broken integration look like a working one"): a missing signal is
being read as a negative signal.

## 2. Class analysis (FIX THE CLASS, NEVER THE INSTANCE)

- **Class of D1:** a state machine whose terminal states do not cover the input
  domain. Fix = add the missing outcome (PILOT) *and* the lane that drains it, not
  a hand-run experiment for this one proposal.
- **Class of D2:** a downstream parser treating producer-side format failure as
  semantic content. Fix = enforce the format at the producer (fail-closed at
  `write_output`, so a malformed note is never written at all) *and* make the
  consumer retry once instead of retiring. Fixing only the consumer leaves the
  producer free to emit malformed notes forever.
- **Where else this class lives:** `_find_note()` returns any file carrying the id
  and `cmd_reap` reads it blind; the same fail-closed gate protects that path too
  once the producer cannot write a formless note. Flagged, not widened, here.

## 3. The four changes

### 3.1 PILOT outcome in `minerva_defer_review.py`

New regex `PILOT_SHAPED` = `pilot|benchmark|A/B|a-b test|measure|trial|compare`.
A defer routes to PILOT when the reason matches it **and** `impact in ('H','M')`.
Order in `cmd_review` matters and is deliberate:

    SETTLED (and not INFO_GAP)  -> retire      (unchanged)
    PILOT_SHAPED and impact H/M -> pilot       (NEW, before RESEARCH)
    INFO_GAP and _value_ok      -> research    (unchanged)
    stale                       -> retire      (unchanged)

PILOT is checked before RESEARCH on purpose: an empirical question that also
matches `INFO_GAP` ("requires a benchmark") should be measured, not read about.
That is the whole point of the ticket.

Effect: writes `/home/lumen/foreman/pilots/<pid>/CARD.md` from the experiment-card
template, opens a `pilot`-lane wq ticket pointing at it, sets the proposal to
`status='piloting'` with the ticket id in `decision_reason`. Capped by
`DEFER_MAX_PILOTS` (default 3/day) so a first run cannot flood the lane.

Reap: `cmd_reap` already sweeps `status='researching'`. It gains a second sweep
over `status='piloting'`, reading `/home/lumen/foreman/pilots/<pid>/REPORT.md` and
running the *same* `_verdict_from()` — ADOPT->accept, WATCH->defer, SKIP->
rejected_aged. One verdict function, two producers; a pilot is not a special case
downstream, which is what makes 3.4 (ship-through to PLAN) free.

### 3.2 Reaper: re-run once, retire only on the second miss

`_verdict_from()` gains a third return state. No `RECOMMENDATION:` line now means
`('rerun', ...)` instead of `('rejected_aged', ...)`. `cmd_reap` on `rerun`:

- first miss: re-queue the prompt once, `deepseek-v4` rung, `rerun=1` recorded in
  `decision_reason`, proposal stays `researching`/`piloting`. The stale note is
  moved aside to `<name>.formfail` so `_find_note` does not re-read it.
- second miss (`rerun=1` already present): retire, reason
  `'no RECOMMENDATION line after one enforced re-run'`. Bounded, never a loop.

### 3.3 Producer-side format gate (fail-closed) in `write_output`

`autonomy/lumen_fleet_graph.py::write_output` is the real implementation the
worker delegates to (`lumen_fleet_researcher.worker` -> `run_task`). It gains a
gate that fires **only** for prompts whose text carries the `RECOMMENDATION:`
contract (i.e. defer-review prompts — matched on the prompt body, not the
filename, so a renamed prompt cannot dodge it):

    contract present in prompt AND no RECOMMENDATION: ADOPT|WATCH|SKIP in result
      -> do NOT write the .md, do NOT move the prompt to done/, status='requeue'

Fail-closed by construction: the malformed note never reaches disk, so the reaper
cannot mis-score what was never written. Scoped to prompts that asked for the
line, so ordinary research reports are unaffected — a blanket gate would requeue
every non-defer report in the fleet.

`autonomy/` and `agent/` are not git repos. This change lands in place with a
timestamped `.bak`, which is the fleet's standard for non-repo paths; the other
three changes land by PR.

### 3.4 Pilot lane

- `router.py`: `LANE_REPO["pilot"] = FOREMAN_ROOT` (pilot artifacts live in
  `foreman/pilots/`), `_LANE_BIAS["pilot"] = ["probe-build", "research-synthesis"]`
  — probe-build wins the run, research-synthesis the write-up, exactly as ordered.
- `dispatcher/config.json`: `"pilot": {"enabled": true, "max_per_day": 3}`. Without
  this entry `wq add --lane pilot` is refused as a mis-lane (wq#912) and the
  dispatcher can never see the tickets.
- `fleet-exec-policy.yaml`: `allow.ssh += hathor`, `copy_dirs.hathor =
  [/home/hathor/pilots]`, `read_dirs += /home/lumen/foreman/pilots`.
- Experiment-card template at `foreman/pilots/EXPERIMENT_CARD.template.md`.

**Corrected detail (verified, not re-litigated).** The ticket names the remote root
as `/home/lumen/pilots on hathor`. There is no `/home/lumen` on hathor — the ssh
user is `hathor` and `$HOME` is `/home/hathor` (`ssh hathor@100.80.71.13 'ls -d
/home/lumen'` -> No such file or directory). The remote root is therefore
`/home/hathor/pilots`, matching every other node's `copy_dirs` convention
(`<node home>/...`). `/home/lumen/foreman/pilots/<pid>/` remains the Lumen-side
root of record for CARD.md and REPORT.md, as the ticket specifies.

**Data that must not cross to a pilot node.** `/home/lumen/minerva` is deliberately
absent from `allow.read_dirs` (credentials are mirrored into its context sections).
`minerva.db` is therefore never copied to a pilot node. A pilot gets a purpose-built
extract containing only the columns it needs, written by the pilot's own extract
step on Lumen. Pilot #1's extract carries proposal ids, titles, proposed_change
text and dedup vectors — no credentials, no `.env`, no context sections.

### 3.5 Re-open the 19

19 rows are `rejected_aged` with `decision_reason LIKE '%no RECOMMENDATION line%'`.
They go back to `status='defer'`, `decided_at=NULL`, reason prefixed
`reopened wq#1559 (form failure, never judged):`, one JSON line each appended to
`minerva/proposals_decisions.log`. The 30 SKIP-with-leaked-reasoning notes are a
separate defect (cheap-model output quality, not form) and are NOT touched here —
naming them without fixing them is what §5 is for.

## 4. Test plan (test to failure, not to first green)

| # | Test | Expected |
|---|---|---|
| T1 | `defer_review.py review --dry` before/after | pilot count > 0, research count unchanged for non-pilot-shaped defers, retired count does not grow |
| T2 | PILOT_SHAPED vs INFO_GAP overlap | a defer matching both routes to pilot, not research |
| T3 | `_verdict_from` unit: note with no RECOMMENDATION | returns `rerun`, not `rejected_aged` |
| T4 | reap with `rerun=1` already set | retires (bounded — proves no loop) |
| T5 | write_output with contract prompt + malformed result | no .md on disk, prompt not in done/, status=requeue |
| T6 | write_output with contract prompt + valid result | writes normally |
| T7 | write_output with ordinary research prompt + no RECOMMENDATION | writes normally (gate scoped, not blanket) |
| T8 | `router.py classify` on a pilot ticket | archetype probe-build |
| T9 | `wq add --lane pilot` | accepted (dispatcher entry present) |
| T10 | fleet-exec policy load + `check_ssh('hathor')` | allowed; `check_restart` on hathor still denied |
| T11 | re-open SQL | exactly 19 rows move, 19 log lines, no other status touched |
| T12 | pilot #1 end to end on hathor | REPORT.md with RECOMMENDATION line + numbers |
| T13 | reap of pilot #1 | proposal leaves `piloting` with the verdict the report gave |

Failure-first cases explicitly exercised: T4 (second miss), T5 (malformed), T7
(scope leak), T10 (policy must still deny what it denied before).

## 5. Known gaps left open (not silently absorbed)

1. The 30 SKIP notes with leaked reasoning are unjudged garbage from a cheap rung.
   Not re-opened here — that is a rung-quality fix, filed separately, not smuggled
   into a form-failure change.
2. Pilot node pool is hathor only. wadjet-via-WoL and the hapi blades are named in
   the ticket as later additions; the lane map is written so adding a node is one
   `allow.ssh`/`copy_dirs` pair, not a redesign.
3. `_find_note()` still matches any file carrying the id. The producer gate makes a
   formless note unwritable, which closes the practical hole, but the loose match
   remains.

## 6. Rollback

Per change, independently:

- **3.1/3.2** `minerva_defer_review.py` — revert the PR. Proposals left in
  `status='piloting'` are inert (nothing else reads that status); a one-line
  `UPDATE proposals SET status='defer' WHERE status='piloting'` returns them.
- **3.3** restore `lumen_fleet_graph.py.bak.<ts>_pre_wq1559` — no service restart
  needed (the graph is imported per task by the worker).
- **3.4** revert the PR. `fleet-exec-policy.yaml` also fails closed on `chmod 000`
  (design §5.1) if the policy itself is ever suspect.
- **3.5** the log lines carry old and new status per id; a reverse UPDATE from the
  log restores all 19.

Nothing here restarts a service, touches vault/cloudflared/Commander, or writes to
prod memory from a pilot. Per the ticket: a pilot report never wires itself in — an
ADOPT feeds PLAN and is built like any other accept.
