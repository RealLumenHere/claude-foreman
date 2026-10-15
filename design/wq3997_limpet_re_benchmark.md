# wq#3997 — adopt a published RE-agent benchmark as Limpet's eval instrument

Source: `design/wq3929_competitive_analysis.md` §A1 (academic RE-agent benchmarks) and the
live-search validation pass `scratch/research/limpet_live_search_2026-10-14.md` PART A1.
Limpet has no external eval instrument; adopting a published benchmark is cheap credibility
and a forcing function, and ties to the fleet golden-set doctrine (the frozen-set, measured-
number posture already used for minerva recall, ISSUE-LOG #292).

## The candidates (from the live search)

- **CrackMeBench** (arXiv 2605.10597) — a corpus of *crackme* challenges: user-space software
  binaries with a license/keygen check to defeat. Single-artifact, x86/software-centric RE.
- **AgentRE-Bench** (agentre-bench.ai) — an *agentic* RE benchmark: an agent is given a target
  and must plan, drive tools, and produce graded findings end to end.
- Survey, "Challenges and Future Directions in Agentic Reverse Engineering Systems"
  (arXiv 2604.14317) — frames agentic-RE evaluation around end-to-end task success rather than
  single-shot decompilation accuracy.

## Decision — adopt AgentRE-Bench as the primary instrument, loader kept benchmark-agnostic

1. **Unit of evaluation matches the product.** Limpet ships an *agent-driven* RE appliance —
   plan → drive tools → produce evidence-backed findings. AgentRE-Bench scores exactly that
   loop. CrackMeBench scores single-binary software-license reversing, a narrower, software-
   centric slice further from the appliance's firmware/hardware emphasis (the ticket's
   selection criterion). The survey corroborates the end-to-end-success framing.
2. **It aligns with the ledger model Limpet already has.** A graded end-to-end task maps onto
   the obligation ledger + critic finding model (`limpet/obligations.py`, `limpet/critic.py`):
   a benchmark task is a set of ground-truth questions, and a finding either answers one or
   does not.
3. **One instrument, many sources.** The loader (`limpet/benchmark.py`) is benchmark-agnostic:
   it reads `[[task]]` tables from `limpet/benchmark.d/*.toml`, each tagged with the
   `benchmark` it is drawn from and a `target_class`. Adopting AgentRE-Bench as the default
   does not foreclose adding CrackMeBench, or a firmware-only subset of either, as additional
   drop-in files — the same one-schema-many-files posture as the target-device catalog
   (`limpet/target-devices.d/`, wq#3940). The appliance's eval focus is then expressed by
   *which drop-ins ship*, not by hard-coding one benchmark into the runner.

## What this ticket ships vs. what is held back (on purpose)

Ships, runnable and tested from inside the build sandbox today:

- `limpet/benchmark.py` — the instrument: load a suite, grade one task against an agent's
  answer, run the suite against an injected agent, a periodic-run loop, and a CLI.
- `limpet/benchmark.d/agentre_fixture.toml` — a small **offline fixture** modeling the
  AgentRE-Bench task schema (firmware/hardware-leaning target classes). This is the committed,
  runnable default the harness and tests exercise; it is **not** the vendored published
  dataset.
- `limpet/test_limpet_benchmark.py` — tests covering load, grade (pass *and* fail), the
  resumable runner, the periodic loop, and the CLI baseline.

Held back, queued rather than guessed around:

- **Vendoring the real AgentRE-Bench task set.** The published dataset must be fetched over
  the network, which the build sandbox cannot do: `fleet-search web` is down fleet-wide
  (wq#2595) and no web tool is granted this session — the exact wall the prior wq#3997 attempt
  hit. Fetching + pinning the dataset (and the final confirmation of its firmware/hardware
  task proportion against the selection criterion above) is a follow-up needing egress, not a
  design gate. The instrument is written benchmark-agnostic so that follow-up is a drop-in
  file, not a code change.
- **Wiring the real Limpet agent.** The agent core is design-only (`design/wq3806_limpet_
  architecture.md` §6; same note in `critic.py`/`model_router.py`). The runner therefore takes
  the agent as an injected callable. With no agent core, the appliance's honest baseline on the
  benchmark is **0** — which the CLI's default `null` agent reports, and which is itself the
  forcing function the ticket wants.

## Periodic run

`limpet/benchmark.py periodic` wraps the single-shot `run_suite` in an injectable-sleep loop,
the same shape as the registry daemon (`limpet/daemon.py`). Results are appended as JSON lines
(`schema: limpet-bench/1`) so a run is resumable — a task whose digest is unchanged since the
last `task_done` is skipped — mirroring the macbench results idiom
(`tools/macbench/README.md`). Deployment wiring (a systemd timer / `lumen-run` detached job on
a cadence) belongs with the dataset follow-up; per fleet instinct 5 the daemon is never run
foreground-unattended.
