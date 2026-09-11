# LPP savestate / checkpoint strategy

wq#1714 · researched 2026-09-11 · live data from `/home/lumen/pokemon/{checkpoints,events,runtime}`
read-only at time of writing; prior art fetched from upstream sources (§3).

## 1. What actually wedged

Two different failures wore the same costume over the 14h window. Both are visible in
`events/live-agent-*.jsonl` boot records and `runtime/resume_history.json`.

**Failure A — the true resume loop (05:33–06:38Z, 2026-09-10).** Three consecutive boots
resumed the identical file:

```
05:33:14Z  resumedFrom auto-mtv31v9v-t20.state.gz
05:51:13Z  resumedFrom auto-mtv31v9v-t20.state.gz
06:38:14Z  resumedFrom auto-mtv31v9v-t20.state.gz
```

`ls checkpoints/ | grep -c auto-mtv31v9v` = **0**. Not one of those three runs ever wrote a
checkpoint of its own. That is the whole mechanism:

- `Agent.maybeCheckpoint` (`src/agent.ts:1656-1661`) writes a periodic auto only at
  `turn % 10 === 0`, or on a map change, or on a milestone. A run that dies before turn 10
  without leaving its map leaves **no trace on disk at all**.
- `pickResume`'s repeat guard (`src/resume.ts:402-421`) demotes a thrice-picked winner only
  if `scored.find(c => c.mtime > winner.mtime)` — i.e. only if the previous run produced
  something newer. It did not. So the guard took its documented `else` branch,
  "repeat guard saw 2 identical picks but no newer alternative — keeping ...", and the boot
  reloaded the same twenty-turn-old state. Forever.

The guard's own comment (`resume.ts:271-276`) claims it "cannot wedge: it demotes exactly
one file, for exactly one selection, and only when a newer alternative exists to take
instead." That last clause is precisely the wedge. The escape hatch is conditioned on the
wedged run being productive enough to escape on its own.

**Failure B — the plateau (12:00Z–01:43Z).** Eight boots, mean run 64 min, every pick a
*different* file, so the repeat guard never engaged and nothing looked wrong. Yet every
sidecar in that window reads the same:

```
auto-mtw6sfhq-t490.json  "map":35,"x":5,"y":24,"badges":1,"events":48,"storyScore":10
auto-mtw9x03s-t80.json   "map":35,"x":5,"y":30,"badges":1,"events":48,"storyScore":10
auto-mtwam49j-t220.json  "map":35,"x":11,"y":35,"badges":1,"events":48
milestone-mtw9x03s-map64-t47.json "map":64,"x":3,"y":3,"badges":1,"events":48
```

map 35 = Route 24, map 64 = Cerulean Pokécenter, one badge, `storyScore` pinned at 10 for
the entire window. Selection was not wrong here — there was nothing to select. Every
candidate was the same progress. Nine hours of compute bought zero story flags, and the
selector, having no way to say "this plateau is not working", faithfully restored the
plateau nine times.

Failure A is a selector bug. Failure B is the selector being asked to solve a problem it
cannot see. The design below addresses both, because in the logs they are indistinguishable
and an operator cannot triage them apart today.

## 2. Why the current selector keeps needing repair

`pickResume` is an eight-level comparator (`resume.ts:373-379`) re-derived from scratch on
every boot across the ~199 files in `checkpoints/`:

```
criticalCondition → badges → storyScore → onWarp → STORY_ORDER rank → mtMoon leg → events → mtime
```

Each level is a scar. The file's own comments date them: wq#471 (raw `events` was gamed by
Pewter museum flags), wq#518 (map 15 is two places), wq#534 (map 60 is four pockets),
wq#586 (Mt Moon route legs), wq#628 (0.6% of one HP bar outvoted a story flag), wq#685
(`STORY_ORDER` ended at Cerulean, 76h frontier freeze), wq#699 (torn mid-map-load
sidecars), wq#1066 (a warp-tile save out-ranked everything and bounced for 180h). Eight
documented regressions, each fixed by appending one more tiebreak.

Two structural properties guarantee a ninth:

1. **`STORY_ORDER` is a hand-maintained list that must be extended at the start of every
   campaign leg** (`resume.ts:19`). It has been forgotten twice — 2026-08-12 (Pewter) and
   2026-08-19 (Cerulean) — and both times the whole campaign silently rewound to the last
   ranked map. A correctness invariant that depends on a human remembering to edit an array
   is not an invariant.
2. **`storyScore` is a popcount, and the flag list contains optional flags.**
   `STORY_FLAGS` (`src/state.ts:42-59`) includes `EVENT_GOT_TOWN_MAP`, `EVENT_GOT_NUGGET`,
   `EVENT_GOT_BICYCLE`, `EVENT_BEAT_ROUTE22_RIVAL_1ST_BATTLE`, and two mutually-substitutable
   fossils — all marked `(optional)` in place. So `storyScore == 10` is not one position:
   picking up the Route 24 Nugget scores exactly as much as beating the Cerulean rival.
   Cardinality over a set containing optional members is not a total order on progress, and
   the comparator treats it as one.

And the one piece of frontier machinery that already exists is not wired to selection:
`maxStoryScore` is stamped into every sidecar (`agent.ts:1683`) and read by
`RecoveryTracker` (`recovery.ts:73`) purely to *tell the model in prose* that it "resumed
BEHIND your best progress" (`recovery.ts:126`). `pickResume` never reads the field — the
`Sidecar` interface at `resume.ts:141-148` does not even declare it. LPP records its
frontier and then ignores it at the only moment it matters.

## 3. Prior art

### 3.1 pokemonred_puffer (drubinstein) — ratchet on a content key

The only one of the four that genuinely *selects* among states, and the most directly
applicable.

- **State identity is the set of required events, not a filename.** In
  `cleanrl_puffer.py:261-272`, states arrive keyed `state/<tuple-of-events>`; the key is
  `ast.literal_eval`'d back into a tuple and used as the dict key. Archived states go to
  `archive_path / str(hash(key))` with a `desc.txt` naming the set.
- **One state per key.** `self.states: dict = defaultdict(partial(deque, maxlen=1))`
  (`cleanrl_puffer.py:143`) — the newest state for each distinct event set, and nothing
  else. There is no 199-file directory to rank.
- **Strict high-water ratchet.** The swarm broadcast walks the keys for the largest
  `len(key)` and fires only `if max_event_count > self.max_event_count`. Once the run has
  broadcast at N events it will never broadcast at ≤ N again, for the life of the process.
  Progress cannot be traded away by a tiebreak.
- **V1 was abandoned for exactly LPP's failure.** The commented-out V1 migrated the bottom
  `swarm_keep_pct` of envs to `random.choice` of the top states, and the comment reads:
  "this has a lot of gotchas and is really unstable / E.g. Some envs could just constantly
  be on the bottom since they're never progressing" (`cleanrl_puffer.py:349-354`). A
  heuristic redistribution that keeps re-picking a non-progressing state is the thing they
  deleted.
- **Per-event wall-clock deadlines.** `config.yaml` `early_stop:` gives each required event
  a minutes budget (`EVENT_BEAT_BROCK: 30`, `EVENT_BEAT_MISTY: 300`, `EVENT_GOT_HM01: 600`).
  A run that has not reached the flag in budget is killed rather than continued. This is the
  only mechanism in any of the four prior-art systems that would have caught Failure B.

### 3.2 PokemonRedExperiments (PWhiddy) — never select at all

`v2/red_gym_env_v2.py:123-127`: every `reset()` unconditionally opens `self.init_state` and
`pyboy.load_state(f)`. One fixed file, forever. Progress lives in policy weights, not in the
savestate, so the resume-selection problem is designed out of existence. Not adoptable by
LPP (there are no weights to carry progress) but it is the cleanest statement of the
principle: **if resume selection can be made constant, make it constant.**

### 3.3 Gemini Plays Pokémon — one continuous session

Joel Zhang's 29-minute build writeup (`blog.jcz.dev/the-making-of-gemini-plays-pokemon`)
does not contain the words "save state", "savestate", "checkpoint", "restart", "resume", or
"rewind" anywhere. Not as an omission — the architecture has no restart. There is one
emulator session that only moves forward; a blackout is handled by the game's own
respawn-at-Center rule, which is what that rule is for.

What GPP treats as the recovery mechanism is **context**, not state: past ~100k tokens the
model "would fall into repetitive loops of behavior instead of coming up with new
strategies", and the fix was truncating the context and re-prompting with a compact
summary — after which "it was able to simply walk down and out of the backyard, no longer
stuck in its repetitive loop." The Pewter-backyard anecdote is behaviourally identical to
LPP's Route 24 plateau, and the intervention that broke it touched zero savestates.

### 3.4 Claude Plays Pokémon — summarization + a model-owned knowledge base

Same shape. Continuity is an "accordion-style summarization approach: the agent takes
approximately 30 actions, then the system generates a summary of that interaction block",
plus a "self-managed persistent memory system embedded in the prompt that the model can add
to, edit, and reference", where "the model has full control over this knowledge base".
Savestate selection is not part of the design.

### 3.5 The synthesis

Four systems, two strategies, zero heuristic comparators:

| system | resume target | how progress is protected |
|---|---|---|
| pokemonred_puffer | frontier state, keyed by event set | strict `>` high-water ratchet on `len(event set)` |
| PokemonRedExperiments | fixed `init_state` | policy weights; state is constant |
| Gemini Plays Pokémon | n/a — continuous session | in-game respawn + context truncation |
| Claude Plays Pokémon | n/a — continuous session | summarization + knowledge base |
| **LPP today** | **best-of-199 by 8-level comparator** | **nothing; every level can trade progress away** |

LPP is the only one ranking a directory. That is why it is the only one with eight
documented ranking regressions.

## 4. Design

Five changes, ordered by the ratio of wedge-risk removed to code touched. P1–P3 are the
deliverable; P4–P5 are the follow-on that makes P1–P3 stop mattering.

### P1 — Frontier ledger, and selection reads it first

New `runtime/frontier.json`, written by the agent, read by `pickResume`:

```json
{ "v": 1,
  "bestKey": ["EVENT_FOLLOWED_OAK_INTO_LAB", "EVENT_GOT_STARTER", "..."],
  "bestBadges": 1,
  "bestFile": "/home/lumen/pokemon/checkpoints/frontier-<hash>.state.gz",
  "promotedAt": "2026-09-10T13:04:31Z",
  "history": [ { "key": [...], "file": "...", "at": "..." } ] }
```

Promotion rule — **strict superset of the flag set, not popcount**:

```
promote(c) iff  setOf(c.storyFlags) ⊋ bestKey
            or (setOf(c.storyFlags) == bestKey and c.badges > bestBadges)
```

`StateReader.storyFlagsSet()` already returns exactly this set (`state.ts:132-134`,
wq#503) — the data exists, it is simply thrown away into a popcount today. Superset
promotion is what kills the optional-flag ambiguity from §2.2: a Nugget can no longer buy
the rank of a rival win, because `{...,NUGGET}` is not a superset of `{...,BEAT_CERULEAN_RIVAL}`.

Selection becomes three cases, in order:

1. `frontier.bestFile` exists, loads, `coordsPlausible` passes, and is not
   `criticalCondition` → **resume it, stop.** No comparator runs.
2. Otherwise → run today's comparator, but only over candidates whose flag set ⊇
   `bestKey`. Non-regressing by construction.
3. Otherwise → today's unrestricted comparator, and emit `frontier_regression` with the
   flags being given up. This is the only path that can lose progress and it is now loud.

`STORY_ORDER` survives as a tiebreak *within one flag set* — where it is a genuine
positional signal — and leaves the correctness path. The forgot-to-extend-the-list class of
regression (wq#685, wq#1066) can no longer rewind a campaign, because case 3 is the only
route to a rewind and it is gated and alarmed.

### P2 — Snapshot on flag acquisition, and never prune those

`maybeCheckpoint` (`agent.ts:1651-1661`) snapshots on badge, on full heal, on map change,
and every 10 turns. Add one trigger, mirroring puffer's `archive_states`: the moment
`storyFlagsSet()` gains a member, write `frontier-<flagset-hash>.state.gz` and promote it.

`pruneAuto` (`agent.ts:1703-1712`) keeps the newest 40 `auto-*` and already spares
`milestone-*`; `frontier-*` joins the spared set. This bounds the canonical state set at
*one file per distinct flag set the campaign has ever reached* — the direct analogue of
puffer's `maxlen=1` deque — and takes the 199-file directory out of the selection path
entirely.

### P3 — A loop breaker that cannot degenerate

Replace the `mtime > winner.mtime` escape hatch (`resume.ts:412-413`). Its defect is that
it asks the wedged run to prove its own productivity before it is allowed to escape.
Condition the breaker on an always-observable fact instead: **did the last boot accomplish
anything.**

Extend `runtime/resume_history.json` entries from `{file, at}` to
`{file, flagsetHash, at, turnsSurvived, flagsGained}`. `turnsSurvived`/`flagsGained` are
written at run end, and backfilled as `0` by the next boot if the run died without writing
them — a run that cannot report is a run that did nothing.

```
unproductive(pick) := pick.flagsGained == 0 && pick.turnsSurvived < 10
```

Ten turns is not arbitrary: below `turn % 10 === 0` a run leaves no periodic checkpoint, so
it is exactly the threshold at which a boot becomes invisible to the current selector.

After **two consecutive unproductive boots at the same flagset hash** (hash, not filename —
Failure B rotated filenames freely), the selector must take a different state, escalating
and logging at each step:

| step | target | regresses? |
|---|---|---|
| a | newest non-warp state at the same flag set | no |
| b | `milestone-*` heal-kind state at the same flag set | no |
| c | frontier state at flag-set−1 (`frontier.history[-2]`) | one flag, capped |
| d | nearest named `saves/*.state.gz` at or below that set | bounded by the curated set |

Step (c) is the only regressing step, it is capped at a single flag, and it exists because
a flag set that has produced two dead boots in a row is evidence the *state*, not the
strategy, is the problem. The old guard had no equivalent and so had nowhere to go.

### P4 — Make a dead boot leave evidence

Write an unconditional `boot-<runTag>-t0.state.gz` + sidecar immediately after resume
settles, before turn 1 (`live_agent.ts:579-598`, after the existing `settleMapLoad`). Cost
is one state write per boot; `pruneAuto` treats `boot-*` like `auto-*`. This makes "the
previous run produced nothing at all" impossible to confuse with "the previous run never
started", and it is what would have surfaced Failure A within one boot instead of three.

### P5 — Stop restarting, or make restarts free

Eight boots in 8.5h, 1659 lifetime service boots and 11936 gameplay continues
(`events/retry_ledger.json`). Every one discards the brain's working context and the agent
re-derives the plan that just failed. This, not checkpoint ranking, is what GPP and CPP
spend their whole architecture on:

- Prefer the **in-process continue** path (`live_agent.ts:679-680`) over the
  exit-and-be-restarted path (`live_agent.ts:138`) wherever the step-budget or stuck
  detector fires. An in-process continue keeps context; a process exit throws it away and
  then pays `pickResume` to guess which state that context belonged to.
- Stamp the boot prompt with the frontier flag set and the **last three unproductive
  attempts at this flag set** (P3 already records them). LPP's `RecoveryTracker` already
  writes "You resumed BEHIND your best progress" (`recovery.ts:126`); the missing half is
  "and here is what the last three attempts from here tried and failed." Without it the
  agent cannot distinguish its first attempt at Route 24 from its ninth.
- Adopt puffer's `early_stop` shape as a plateau alarm rather than a kill: if the flag set
  has not grown in N hours, emit `story_plateau` and page. Failure B ran nine hours with
  every dashboard green. Puffer would have tripped `EVENT_BEAT_MISTY: 300` minutes.

### Deliberately not adopted

- **Puffer's swarm migration** — it redistributes across N parallel environments. LPP runs
  one. The ratchet transfers; the migration does not.
- **PokemonRedExperiments' fixed `init_state`** — LPP has no policy weights, so a constant
  resume target would discard the campaign.
- **A per-lineage high-water mark** — already analysed and rejected in `resume.ts:261-269`,
  and the reasoning is correct: every descendant of a resume inherits the ancestor's
  high-water, all candidates tie, and selection falls through to raw rank. P1 avoids this
  by keying on the flag set read from *each state's own RAM*, never inherited.

## 5. Acceptance

Replayable against the data that produced this document:

1. **Failure A regression test.** Fixture: `runtime/resume_history.json` with two
   consecutive unproductive picks of one file, and a checkpoint dir in which that file is
   the comparator winner and no newer state exists. Assert the third `pickResume` returns a
   *different* file and emits `resume_loop_broken`. Today's selector returns the same file;
   `resume.ts:419-421` is the branch under test.
2. **Optional-flag ordering.** Two sidecars at `storyScore == 10`, one holding
   `EVENT_GOT_NUGGET` and one holding `EVENT_BEAT_CERULEAN_RIVAL`. Assert neither is
   promoted over the other (neither set is a superset), and that a state holding both is.
3. **No silent regression.** Over any boot sequence, assert every resumed state's flag set
   is ⊇ `frontier.bestKey`, or `frontier_regression` was emitted naming the lost flags.
4. **Plateau visibility.** Replay the 12:00Z–01:43Z boot sequence; assert `story_plateau`
   fires. Nine hours at `storyScore == 10` must not be silent.
5. **Dead-boot evidence.** Assert a run killed before turn 1 still leaves
   `boot-<runTag>-t0.state.gz` and a history entry with `turnsSurvived == 0`.

## Sources

- [pokemonred_puffer `cleanrl_puffer.py`](https://github.com/drubinstein/pokemonred_puffer/blob/main/pokemonred_puffer/cleanrl_puffer.py)
- [pokemonred_puffer `config.yaml`](https://github.com/drubinstein/pokemonred_puffer/blob/main/config.yaml)
- [pokemonred_puffer `wrappers/sqlite.py`](https://github.com/drubinstein/pokemonred_puffer/blob/main/pokemonred_puffer/wrappers/sqlite.py)
- [PokemonRedExperiments `v2/red_gym_env_v2.py`](https://github.com/PWhiddy/PokemonRedExperiments/blob/master/v2/red_gym_env_v2.py)
- [The Making of Gemini Plays Pokémon — Joel Zhang](https://blog.jcz.dev/the-making-of-gemini-plays-pokemon)
- [Building and deploying a Pokemon-playing LLM agent at Anthropic — ZenML LLMOps database](https://www.zenml.io/llmops-database/building-and-deploying-a-pokemon-playing-llm-agent-at-anthropic)
