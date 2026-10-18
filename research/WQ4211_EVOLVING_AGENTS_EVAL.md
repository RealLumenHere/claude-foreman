# wq#4211: evolving-agents (EvolvingAgentsLabs) — agent evolution/mutation framework, dissected

## Verdict: do NOT adopt the framework; adopt three of its patterns.

The ticket's framing ("agents evolve and mutate capabilities over time") describes the repo's
**legacy 2025 layer**, which the lab itself has shelved. The repository
(github.com/EvolvingAgentsLabs/evolving-agents, cloned at 4060a35, 2026-10-17) pivoted: the
active piece is now the **director**, a meta-agent that compiles operational rules into a
linked memory, plans by walking it, and learns via human-gated, versioned, probe-tested memory
patches. The evolution toolkit survives only as a trimmed snapshot under `legacy/eat/` —
MongoDB-backed, BeeAI/OpenAI-agents-coupled, and missing the modules its own imports name
(`tools/`, `monitoring/` are gone from the tree). There is nothing to depend on; there is a
lot to copy.

## 1. Architecture, per the ticket's questions

### How do agents specialize?

Two generations, two answers:

**Legacy (EAT toolkit):** components (agents + tools) live as records in a MongoDB
`SmartLibrary` with `version`, `parent_id`/`evolved_from` lineage, and per-record
`usage_count`/`success_count`/`fail_count`. Specialization is **LLM-rewrite, not GA-style
random mutation**: `evolve_record()` takes a parent, a new LLM-generated code snippet, bumps
the version, preserves lineage, and resets the counters
(`legacy/eat/evolving_agents/smart_library/smart_library.py:590-641`). A `SystemAgent`
chooses between reuse / evolve / create using semantic search over the library. Demos name
strategy labels for the rewrite prompt (e.g. `domain_adaptation` —
`legacy/eat/examples/agent_evolution/openai_agent_evolution_demo.py:647`), but the
`EvolveComponentTool` implementing them is one of the trimmed modules.

**Current (director + lora-kernel):** specialization moved out of prompt-rewriting into
(a) **local specialists** — one small resident model with per-domain LoRA adapters, a
library, scoped tools, and a router that *abstains to the frontier model* when out of domain
(lora-kernel repo; measured 70/70 vs 27/70 for base on-domain); and (b) **lane routing** —
the director plans each task onto the frontier lane (Claude Code via `claude -p`) or the
local lane, and *learns the lane from operator moves* (below).

### How is performance tracked?

- Legacy: per-component success/fail counters in the library record; a MongoDB
  `eat_agent_experiences` collection (goal, sub-task, decisions, outcome, feedback —
  `legacy/eat/eat_agent_experiences_schema.md`); and an A/B-test collection comparing a
  parent against its evolved child.
- Current: a pinned eval suite (D0's 14 situations) re-scored on every memory change; each
  lesson ships with **two self-probes** written at reflection time; and **agentvcs**
  (sibling repo) gives `blame`/`bisect` over memory patches, so a score delta is attributed
  to the one patch that caused it (claimed: blame credited +0.442 to exactly the mid-run
  patch).

### What mutation/selection strategies exist?

**Legacy selection** (`legacy/eat/evolving_agents/evolution/evolution_strategist.py:99-195`):
candidates are additive-priority-scored — low success rate +20, high failed invocations +15,
potentially orphaned +10, unused +5; then A/B results adjust: child beat parent by >10% → +15
to the child, child failed/stagnated → +5 to the *parent* ("re-evaluate for a new strategy").
Top-N by score become evolution candidates. Promotion is explicit:
`deploy_component_version()` activates the child and archives the parent
(`smart_library.py:668-697`). So the loop is: counters → audit → prioritized candidates →
LLM rewrite → A/B test → gated promotion. Selection pressure is real; the "mutation operator"
is just a prompted rewrite.

**Current mutation = gated memory patches** (the interesting part):

1. After a task, `reflect()` has the frontier model draft edits *plus two probes* from the
   episode (`packages/director/director/learn.py:63-76`).
2. `leak()` rejects a probe sharing an 8-word run with the episode — a probe that quotes the
   incident tests recall, not the lesson (`learn.py:78-85`). This is our wq#2630
   "acceptance probe must be able to fail" doctrine, mechanized.
3. Accept gate (`learn_gate.py`): candidate pages must lint clean; a **targeted guard**
   re-plans only the pinned-suite cases whose plans *cited a statement the edit touches*
   (US$1–4 instead of US$13 full-suite); and a **positive probe gate** — the lesson is kept
   only if the candidate memory scores ≥0.67 and ≥+0.33 over current on its own probes
   (TEACHES); REDUNDANT and NO EFFECT are refused. Human accept is still required
   (`human_accepted` is part of the gate metrics).
4. The accepted edit lands as an agentvcs patch — diffable, blameable, bisectable.

**Lane learning (D3):** while a plan waits, the operator can move any task between lanes;
the move is logged to `lane-moves.jsonl` with the task's prompt and request context
(`packages/director/director/core.py:280-301`) and fed to the next reflection, which writes
a routing statement into memory. Measured: 0/6 → 6/6 correct lane after *one* move.

## 2. Can we apply evolution to fworker specialization?

Yes — the selection layer, not the rewrite layer. Mapping:

| EAT concept | fleet equivalent we already have |
|---|---|
| component success/fail counters | wq done/rework history, critic verdicts, episode store |
| EvolutionStrategist priority scoring | nothing — **adoptable gap** |
| A/B parent vs evolved child | nothing systematic — adoptable for worker-prompt variants |
| gated promotion, archive parent | lumen-pr + critic gate (per-ticket, not per-profile) |
| lane learning from operator moves | `research/DIFFICULTY_ROUTING.md` + models.yaml routing chains — currently hand-edited, not learned |

Concrete, cheap first step: an **evolution-strategist pass over the episode store** — score
(ticket-class × worker-profile) cells by failure rate and rework count with EAT's additive
bonuses, and emit the top-N as *proposed* profile edits (prompt tweaks, model-routing changes)
to minerva/dream. That respects instinct 7 (proposal-only consolidation) exactly the way the
director's human-gated Accept does. Per-worker specialization ("some agents get better at
code, others at research, others at RE") then falls out as routing: profiles whose cell stats
beat the pool on a ticket class get that class preferentially — D3's lane-move mechanism, with
"operator moved the task" replaced by "critic passed/failed the ticket".

What **not** to copy: the legacy LLM-rewrite-the-agent-code loop. Our workers are prompt +
allowlist + model, regenerated per dispatch; rewriting live agent code and A/B-ing it in prod
is the part the lab itself abandoned for gated memory edits.

## 3. Can Limpet agents evolve tool preferences by success rate?

Yes, and this is the best fit. The lora-kernel/local-lane pattern maps directly:

- Keep EAT-style counters per (tool × target-device-class): usage/success/fail, plus an
  error-type summary. Limpet runs are already structured enough to emit this.
- Tool choice becomes a ranked preference read from the counters, with an **abstain path**:
  when no tool clears a success-rate floor for the device class, escalate to the frontier
  model / a human queue rather than thrash. lora-kernel's measured caveat applies and is
  worth writing on the wall: *the specialist's abstention was not reliable outside the
  phrasing it was measured on* (packages/director/README.md:139) — so gate abstention on
  counters (hard data), not on the model's self-report.
- Preference *changes* should be versioned patches with a pinned probe suite (a fixture set
  of known devices), not silent counter drift — the D7 "TEACHES" gate: a preference edit is
  kept only if it improves the probes it was written against and doesn't regress the pinned
  suite. That makes every tool-preference change blameable and bisectable, which matters the
  day a Limpet agent starts bricking a device class.

## 4. What the numbers actually support

The lab's headline numbers are self-reported but unusually disciplined: pinned suites,
briefs per claim, flat results published ("including the ones that came back flat"), and a
targeted guard validated against *planted breaks*. Rule-following 0.589 → 0.929 by compiling
CLAUDE.md into walked, linked statements is the one result with direct fleet relevance —
our instincts/doctrine block is exactly the kind of prose the director compiles — but it is
measured on their 14 private situations, so treat it as a hypothesis, not a transferable
number. The compile costs ~US$5/60 files and plans cost US$1–4 per guarded accept; a fleet
trial would be a separate ticket with its own probe suite.

## Recommendation

1. **No dependency.** Legacy toolkit is paused and incomplete in-tree; the director is a
   Claude Code plugin coupled to interactive Accept flows we don't run headless.
2. **Adopt pattern A (cheap, now):** EAT's additive-priority evolution-candidate scoring
   over the episode store, output to minerva/dream as proposals.
3. **Adopt pattern B (Limpet):** per-(tool × device-class) success counters + counter-gated
   abstain/escalate, with preference edits as versioned, probe-gated patches.
4. **Adopt pattern C (later):** D3-style lane/model routing learned from critic outcomes
   instead of hand-edited models.yaml chains; and the leak() rule — any learned-lesson probe
   that quotes its source episode is invalid — folded into our acceptance-probe doctrine.

Clone analyzed at `/tmp/fworker-wq4211/evolving-agents` (depth-1, 4060a35, 2026-10-17).
Refs: wq#4211. Related: `research/AGENTFORGE_EVAL.md`, `research/DIFFICULTY_ROUTING.md`,
wq#2630 (probe doctrine), instinct 7 (proposal-only memory).
