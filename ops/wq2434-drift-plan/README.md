# wq#2434 — drift calibration plans for LiteLLM rungs

Minerva proposal `9f2866bcda22`, factory plan `ff23cf7bdebb`, lane RED,
change_type `new_standalone_script`. Source intel: *Production AI Drift Planner:
Monitoring Plans and Calibration Boundaries* (10.5281/zenodo.22869651) — used as a
**metric naming reference only**. No browser app, no vendored bundle.

## The gap

The observability stack is already there: LiteLLM `:4000` routes inference,
Langfuse traces it, VictoriaLogs aggregates, Grafana renders, and
`lumen-model-watchdog` / `lumen-model-audit` probe liveness and known-answer
correctness. What is missing is the **policy**: how often a rung should be
sampled, over what baseline, against how many samples, who owns it, and what it
falls back to. Those live implicitly inside code today. This bundle records them
as reviewable JSON, one file per rung.

Nothing here alerts, gates, or routes traffic. A plan is an artifact.

## What gets installed

| artifact | path |
| --- | --- |
| generator | `/home/lumen/minerva/drift_plan.py` |
| plans | `/home/lumen/minerva/drift_plans/<rung>.json` |
| dashboard feed | `/home/lumen/minerva/drift_plans/index.json` |
| read-only route | `GET /api/v1/drift-plans` on `lumen-dashboard` (`:8123`) |
| weekly regen | `lumen-drift-plan.timer` (Mon 04:30, `Persistent=true`) |
| human input | `/home/lumen/minerva/drift_plan_overrides.json` (optional, hand-written) |

Rungs are enumerated from `model_list` in `/home/lumen/agent/litellm_config.yaml`
— 41 rungs as of 2026-09-24.

## Metric catalog and its real sources

`drift_plan.py --list-metrics` prints this live. Every metric names the telemetry
that could actually compute it on this fleet:

| metric | source | applies to |
| --- | --- | --- |
| `latency_p95_s` | Langfuse | all |
| `task_success_rate` | `model_audit` | all |
| `schema_conformance_rate` | `model_audit` | all |
| `benign_refusal_rate` | Langfuse traces | all |
| `input_distribution_shift` | Langfuse traces | all |
| `retrieval_recall` | pgvector + `memory_audit` | rag |
| `grounding_rate` | pgvector + `memory_audit` | rag |
| `corpus_update_lag_h` | `memory_audit` | rag |
| `unauthorized_tool_effects` | **none** | tool-agent |

`unauthorized_tool_effects` is emitted with `"threshold": null` and
`"no_telemetry_source": true`. Nothing on this fleet observes tool side effects,
so a threshold on it would be unverifiable — setting one is a hard error, not a
clamp.

## Lifecycle

Plans are born `DRAFT_REQUIRES_CALIBRATION` with every tunable `null`. A human
edits `drift_plan_overrides.json`; the plan flips to `CALIBRATED` only once
`reviewer`, `rollback_target` and all five sizing fields
(`daily_volume`, `eval_sampling_pct`, `baseline_days`, `observation_window`,
`min_sample_target`) are set.

**Regeneration merges, never overwrites.** The weekly timer preserves human-set
fields from the existing plan file, and `first_generated_at` is kept as the
observation clock. `index.json` flags any draft whose `observation_window` has
elapsed with no reviewer sign-off (`signoff_overdue`).

`system_type` is `chat` unless the LiteLLM entry declares `mode: embedding`
(→ `rag`). `tool-agent` is not detectable from the gateway config and must be
declared in the overrides file rather than guessed.

### Calibrating a rung

```jsonc
// /home/lumen/minerva/drift_plan_overrides.json
{
  "cerebras-oss-120b": {
    "daily_volume": 1200,
    "eval_sampling_pct": 5.0,
    "baseline_days": 14,
    "observation_window": 30,
    "min_sample_target": 384,
    "reviewer": "frank",
    "rollback_target": "deepseek-v4-pro",
    "metrics": { "latency_p95_s": 12.5, "task_success_rate": 0.75 }
  }
}
```

Top-level keys starting with `_` are ignored, so a `"_comment"` note survives in
the file (`drift_plan_overrides.example.json` is a working starting point). Every
value is bounds-checked against the catalog. An unknown rung, unknown
metric, a metric that does not apply to that `system_type`, a `rollback_target`
that is not a real rung, or a number outside its declared `[min, max]` exits 2
and writes **nothing** — the generator fails closed rather than half-applying.

## Apply / verify / rollback

```bash
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq2434-drift-plan/install.sh --verify'  # read-only
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq2434-drift-plan/install.sh'
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq2434-drift-plan/rollback.sh'
```

`install.sh` runs the tests, dry-runs against the live config, then installs; it
backs up `dashboard_api.py` before patching and refuses to install a version that
does not compile. Health is asserted in-script: timer active, dashboard active,
a real generation run, `index.json` non-empty, and the route serving
`available: true`.

The dashboard patch is marker-guarded (`# --- wq#2434 drift plans ---`),
idempotent, and inserted **above** the `if __name__ == "__main__":` block —
`dashboard_api.py` is started as `python3 dashboard_api.py`, so anything appended
below that guard would be defined only after `uvicorn.run()` already had the app
and the route would never register.

## Deliberately not done

- **No HTML card on the dashboard page.** The aggregator surface is the API
  route; the page's 1000-line inline JS template is a separate, riskier edit and
  is not needed for the plans to be reviewable.
- **No alerting.** Proposal step 4: no threshold is wired into anything until a
  human has calibrated at least one rung.
- **A skipped rung exits 0.** A rung name that is not a safe filename is recorded
  in `index.json` as `skipped_rungs` with a stderr `WARN`, not a failed unit —
  `research/FLEET_SERVICE_ATLAS.md` finding 3: guards that exit 1 to signal a
  finding train everyone to ignore red.

## Retirement check (proposal step 5)

Two weeks after calibration, check with `model_audit` whether any declared
threshold would have fired on a real incident. If none would have, retire the
generator rather than expanding it.
