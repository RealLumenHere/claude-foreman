# wq#4098 — LIMPET RE-agent benchmark: adopted instrument + baseline run

**Standing eval:** AgentRE-Bench (`agentre-bench.ai`), adopted in wq#3997 as Limpet's
RE-agent eval instrument (`limpet/benchmark.py`, design `design/wq3997_limpet_re_benchmark.md`).
This report is the deliverable wq#4098 adds on top: the **published baseline score** and the
CREBench reference. Publishing the number is the point — it is cheaper external credibility
and a sharper forcing function than any feature list in positioning.

## Why AgentRE-Bench (recap)

Its unit of evaluation — an agent that plans, drives tools, and produces graded findings end
to end — is exactly what the Limpet appliance ships. CrackMeBench (single-binary software
license reversing) and CREBench (single-binary crypto identification) are narrower; both are
kept as candidate drop-in slices, not the primary instrument. The loader is benchmark-agnostic
(`[[task]]` tables in `limpet/benchmark.d/*.toml`), so which benchmark the appliance is measured
against is a question of which files ship, not of code.

## Baseline run

Suite: `limpet/benchmark.d/agentre_fixture.toml` — a committed offline fixture modeling the
AgentRE-Bench schema (5 tasks: 3 firmware, 2 binary). The real published dataset is a queued
follow-up needing network egress the build sandbox lacks (`fleet-search web` down fleet-wide,
wq#2595).

Reproduce (from `limpet/`):

```
python3 benchmark.py run --agent null      # honest baseline
python3 benchmark.py run --agent oracle    # harness self-test
python3 -m pytest test_limpet_benchmark.py # 12 passed
```

| agent  | score | passed/total | firmware | binary | meaning |
|--------|-------|--------------|----------|--------|---------|
| `null` | **0.0000** | 0/5 | 0/3 | 0/2 | no agent core yet — the honest baseline, and the forcing function the ticket wants |
| `oracle` | **1.0000** | 5/5 | 3/3 | 2/2 | returns ground truth — proves loader + fixture + scorer agree (harness self-test, not a real score) |

Results schema: `limpet-bench/1`. The `null` run's five `task_done` records (all `passed:
false`, nothing matched) are the baseline evidence; the `oracle` run inverts to all-passed,
confirming the scorer can both fail and pass (an acceptance probe that can fail, wq#2630).

## Golden-set tie-in

This is the first instance of the fleet golden-set posture for Limpet: a **frozen fixture**, a
**measured number**, and a **reproduce command** — the same frozen-set / measured-recall model
used for minerva recall (ISSUE-LOG #292). The baseline will move off 0.0 only when the Limpet
agent core (design-only today, `design/wq3806_limpet_architecture.md` §6) is wired into
`run_suite` as the injected agent; until then 0.0 is the honest published score.

## References

- **AgentRE-Bench** — `agentre-bench.ai` (adopted primary instrument).
- **CrackMeBench** — arXiv 2605.10597 (user-space crackme/license RE; candidate slice).
- **CREBench** — arXiv 2604.03750 (crypto binary RE; candidate slice, noted per wq#4098).
- **Survey** — arXiv 2604.14317, "Challenges and Future Directions in Agentic Reverse
  Engineering Systems" (end-to-end task-success framing).

## Follow-ups (queued, not gated)

1. Vendor + rev-pin the real AgentRE-Bench task set once web egress exists (wq#2595).
2. Wire the Limpet agent core into `run_suite` to replace the `null` baseline with a real score.
3. Deploy `benchmark.py periodic` as a detached `lumen-run`/systemd-timer job (instinct 5:
   never foreground-unattended).
