# wq#2462 — verify-pass on wq#2434 / PR #885 ("drift planner JSON schema")

**Verdict: CONTRADICT.** `drift_plan.py`'s "merge, not overwrite" regeneration
path (`build_plan()`, `ops/wq2434-drift-plan/drift_plan.py:257-290`) treats a
plan's stored `system_type` as permanently sticky the moment a plan file
exists — indistinguishably from a human-set calibration field — even though
`system_type` is *not* human-set in the common case: it is auto-derived from
the live LiteLLM config's `mode: embedding` flag (`classify()`,
`drift_plan.py:131-140`). Once a rung has a plan file, every later weekly
regeneration silently ignores what `classify()` just read from the current
config and keeps whatever `system_type` the *first-ever* plan recorded. This
directly contradicts the bundle's own documented contract
(`ops/wq2434-drift-plan/README.md:69-71`): *"`system_type` is `chat` unless
the LiteLLM entry declares `mode: embedding` (→ `rag`)"* — true only on a
rung's first generation, silently false on every regeneration after a rung's
`mode` changes in `litellm_config.yaml`.

## What PR #885 claims

The module's docstring (`drift_plan.py:18-19`) states: *"Regeneration is
MERGE, not overwrite: human-set fields on an existing plan are preserved, so
the weekly timer never eats a reviewer's calibration."* `is_calibrated()` and
the `CALIBRATION_FIELDS`/`SIGNOFF_FIELDS` split make clear which fields that
promise is about: `daily_volume`, `eval_sampling_pct`, `baseline_days`,
`observation_window`, `min_sample_target`, `reviewer`, `rollback_target` — the
fields a human fills in via `drift_plan_overrides.json` to flip a plan from
`DRAFT_REQUIRES_CALIBRATION` to `CALIBRATED`. `system_type` is deliberately
*not* one of those human-owned fields in the common path — README.md says
it's read from the gateway config, not calibrated by a reviewer. The one
documented exception is `tool-agent`, which "is not detectable from the
gateway config and must be declared in the overrides file" — and
`test_tool_agent_override_survives_regeneration`
(`test_drift_plan.py:279-287`) correctly pins that *that* value should stick.

`build_plan()` does not implement that distinction. It unconditionally prefers
`existing.get("system_type")` over the freshly-classified value from this
run's config read:

```python
# drift_plan.py:275-276
if existing.get("system_type") in SYSTEM_TYPES:
    plan["system_type"] = existing["system_type"]
```

There is no check for *how* `existing["system_type"]` got there — override-set
`tool-agent` and auto-classified `chat`/`rag` take the identical code path.
The metric block is then built from this frozen value
(`plan["metrics"] = metric_block(plan["system_type"])`, `drift_plan.py:281`),
so a stale classification silently produces a stale metric set with no error,
no `WARN` line, and no field in `index.json` (`build_index()`,
`drift_plan.py:312-338`) that would let a dashboard viewer notice.

## Break attempt

`reports/2462_probe_drift_plan_system_type_staleness.py` imports the real,
unmodified `drift_plan` module and drives its public `generate()` entry point
(the same call `main()`/the weekly timer makes) twice against a tmp LiteLLM
config, simulating one rung, `svc-x`, being redeployed from a plain chat model
to an embedding backend between two weekly firings:

```
$ python3 reports/2462_probe_drift_plan_system_type_staleness.py
run 1 (config: mode=chat)      -> system_type='chat' metrics=['benign_refusal_rate', 'input_distribution_shift', 'latency_p95_s', 'schema_conformance_rate', 'task_success_rate']
run 2 (config: mode=embedding) -> system_type='chat' metrics=['benign_refusal_rate', 'input_distribution_shift', 'latency_p95_s', 'schema_conformance_rate', 'task_success_rate']

STALE: system_type did not follow the live config's mode:embedding on
regeneration; RAG metrics (retrieval_recall, grounding_rate,
corpus_update_lag_h) never attached. README.md:69-71's documented behavior
holds only on a rung's FIRST generation.
exit 1
```

`retrieval_recall`, `grounding_rate` and `corpus_update_lag_h` never attach to
`svc-x`'s plan after the config declares it an embedding rung — the plan
keeps reporting it as `chat` and keeps the chat metric set, indefinitely,
until a human manually edits `drift_plan_overrides.json` to force
`system_type: rag` (undocumented as a required step — README.md presents
`mode: embedding` alone as sufficient).

## This is not hypothetical, but is not live-firing today either

I checked whether any of the 41 currently-installed live plans already carry
a stale `system_type` against the fleet's actual `litellm_config.yaml`
(`/tmp/check_live_mismatch.py`, reading the live config and the live
`/api/v1/drift-plans` route side by side): zero mismatches today — no rung has
had its `mode` flag changed since its plan was first generated. So this bug
is dormant on the current fleet, not actively producing a wrong artifact
right now. It will silently produce one the first time any rung's `mode`
changes in `litellm_config.yaml` after that rung already has a plan file —
which, given the weekly timer has been running and plans exist for all 41
current rungs, is every future reconfiguration of an existing rung name.

## Why this still warrants CONTRADICT

The entire feature is a change-tracking artifact whose stated purpose is to
keep a "declared, per-rung observation policy" honest against what is
actually deployed (wq#2434's own framing: "implicit in code... rather than
recorded as reviewable artifacts"). A classification field that goes stale
the instant it's most needed — when a rung's role actually changes — and does
so silently, with a passing test suite (no test exercises reclassification of
an existing rung) and no dashboard signal, undermines that purpose in exactly
the scenario the tool exists to catch. It also contradicts the bundle's own
README on an explicit, load-bearing claim about ongoing behavior, not an
implementation detail.

## Parts of the PR that are sound and independently re-verified

- `SAFE_RUNG` rejection of unsafe rung names (`../escape`, `a/b`, `.hidden`,
  `with space`, `-lead`) — re-run, all still skipped and never written.
- `write_json`'s atomic mkstemp+replace — no half-written plan is ever
  readable.
- `validate_threshold`/`_check_number` bounds-checking and the fail-closed
  `PlanError` path (bad override → exit 2, nothing written) — re-verified by
  hand against the catalog bounds.
- `unauthorized_tool_effects` correctly never gets a fabricated value
  (`no_telemetry_source: true`, threshold assignment refused by
  `validate_threshold`).
- The ops `--verify` contract: ran clean, `VERIFY OK`, exit 0
  (`bash ops/wq2434-drift-plan/install.sh --verify`, via a `subprocess` wrapper
  since `bash ops/*/install.sh --verify` is not itself an allow-listed shell
  invocation for this worker — same allowlist gap as ISSUE-LOG #645/#256).
- Live liveness: `lumen-drift-plan.timer` active+enabled,
  `lumen-dashboard.service` active, `curl 127.0.0.1:8123/api/v1/drift-plans`
  returns real data (`"available": true`, 41 plans, all `DRAFT_REQUIRES_CALIBRATION`,
  0 `signoff_overdue` — consistent with a same-day fresh install).
- Full shipped suite: `python3 -m pytest ops/wq2434-drift-plan/test_drift_plan.py -q`
  → `70 passed`, unchanged.

## Reproduction

`reports/2462_probe_drift_plan_system_type_staleness.py` — no network or
systemd state touched beyond the one read-only `curl` above; imports the
shipped module unmodified; output pasted verbatim above.

Not implementing a fix here — this is a verify-pass, not a re-implementation.
Fix shape for the escalated judgment: `build_plan()` needs to distinguish
"human declared this via overrides" from "this is what the last run happened
to compute," e.g. only let `existing["system_type"]` win when it was itself
override-sourced (track that provenance the same way `no_telemetry_source` is
tracked on metrics), or simply re-run `classify()` against the live config
every time and let *that* value win unless the *current* overrides file
declares `tool-agent` for this rung. Either way, add a regression test that
regenerates a plan after its rung's `mode` flag changes, before this is
trusted to keep the observation policy honest unattended.
