# Worker-quality eval (wq#1300)

Measures what wq#1289's evidence-gated done is meant to fix (`/home/lumen/kb/ISSUE-LOG.md`
#17, FALSE_COMPLETE): workers claiming done prematurely, and rework not converging.

Two independent measurements, one directory:

1. **False-complete rate of the done-claim critic** — a [promptfoo](https://promptfoo.dev)
   suite (`promptfooconfig.yaml` + `judge_prompt.md`) that feeds a ticket + a worker's
   done-claim to a judge model and scores its `done`/`rework` verdict against ground truth
   (`grader.py`, aggregated by `analyze_results.py`). This is an LLM eval — it needs a
   model call per case, run via `run_eval.sh` against the LiteLLM gateway.
2. **Rework convergence** — `rework_convergence.py` reads real `attempt-N.json` records
   fworker already writes under `$FOREMAN_RUNS_ROOT/wq<id>/` and reports how many attempts
   it took a ticket to leave `FALSE_COMPLETE`. Pure filesystem analysis, no LLM calls, no
   network — run it anywhere.

## Ground truth

`gen_dataset.py` builds the promptfoo dataset from two sources:

- **Real tickets** (`load_real_cases`): `tasks.notes` from `queue.db` as the worker's
  done-claim, labeled `rework` if `tasks.reopened_count > 0` (wq#841's durable rework
  signal — same column `reports/opsaudit_a13_flow_metrics.py` uses, same
  `REWORK_EPOCH` = 2026-08-23 cutoff, for the same reason: reopens before that date were
  never recorded structurally). **Known limitation**: `wq done` overwrites `notes`
  wholesale, so only the *final* claim on a ticket survives — a ticket's `rework` label
  means *some* claim on it was false-complete, not necessarily the one in `notes` today.
- **Synthetic fixtures** (`SYNTHETIC_CASES`): hand-authored cases covering shapes real
  notes rarely spell out — fabricated file:line citations, evidence that contradicts its
  own claim, partial criteria coverage, a converged rework attempt. These also give the
  suite forward-compatible coverage of wq#1289's `acceptance.json`/`evidence.json` shape
  (`/home/lumen/kb/ISSUE-SOLUTIONS-fable.md` §1) once that lands — the judge prompt reads
  either prose notes or structured evidence JSON the same way.

Run `python3 gen_dataset.py` standalone to regenerate `runtime/dataset.yaml`; `--no-real`
skips the `queue.db` read (synthetic-only, useful when the DB isn't reachable).

## Running

**Always go through `run_eval.sh`**, never call `promptfoo eval` directly — the script is
the only place the bridge-safety and concurrency guards live:

```sh
LITELLM_BASE_URL=http://localhost:4000 WQ1300_MAX_CONCURRENCY=2 ./run_eval.sh
```

- Refuses to run unless `LITELLM_BASE_URL` is loopback (`localhost`/`127.0.0.1`/`::1`).
  This suite must run **co-located with the LiteLLM gateway on the inference node** — never
  across the wifi bridge to a router (`kb/tooling-findings-review.md` §4.5,
  `kb/fleet-upgrade-plan-review.md` §4).
- Caps concurrency to `WQ1300_MAX_CONCURRENCY` (default 2, max 4).
- Never calls `ollama pull` and never targets a router/bridge address — the only network
  call this suite makes is to the local LiteLLM gateway's `/v1/chat/completions`.
- `./run_eval.sh --check-only` validates the guards without running anything (used by
  `tests/test_run_eval_guard.py`).

Schedule it **off-peak, on the inference node**, e.g. a cron entry co-located with the
gateway (not on cron from a node that would cross the bridge to reach it):

```
0 4 * * * cd /home/lumen/foreman/harness/evals/worker-quality && ./run_eval.sh >> runtime/cron.log 2>&1
```

For rework convergence (no LLM, no bridge risk, safe from any node with filesystem access
to `$FOREMAN_RUNS_ROOT`):

```sh
python3 rework_convergence.py
```

## Prerequisites

`promptfoo` (MIT, `kb/tooling-findings.md`) is invoked via `npx --yes promptfoo` — no
global install required, but it does fetch the package from the npm registry on first run.
Run that first `npx` once interactively (or `npm install -g promptfoo` once) before adding
this to cron, so a cold cron run never blocks on a registry fetch.

## Interpreting results

`analyze_results.py runtime/results.json` reports:

- `false_complete_rate_pct` — of the cases labeled "should have been rework", the fraction
  the judge wrongly accepted as `done`. This is the number wq#1289 exists to drive toward
  zero (ISSUE-SOLUTIONS-fable.md §1: *"the done-then-critic-reverted count ... goes to
  zero"*).
- `accuracy_pct` — overall judge accuracy across both labels.

`rework_convergence.py` reports `convergence_rate_le2_pct` — the fraction of tickets that
left `FALSE_COMPLETE` within 2 attempts, the target ISSUE-SOLUTIONS-fable.md §1 names
directly (*"rework converging in <=2 attempts"*). As of this writing (see `git log` for
when this was measured), the real fleet baseline is well under that — re-run after wq#1289
lands to see the delta.

## Tests

```sh
python3 -m pytest tests/ -q
```

Pure unit tests against fixture SQLite DBs and fixture `attempt-N.json` trees — no network,
no real `queue.db`/`_runs` mutation, no LLM calls.
