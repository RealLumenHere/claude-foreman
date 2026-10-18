# wq#4220 — deepseek-flash rename: revert the id, make `legacy_aliases` real, close the gate gap

Judgment ticket off wq#4217's CONTRADICT of wq#4210 (PR#2138). Fix shape **(b)** —
revert the manifest id — plus the two pieces wq#4217 said either shape needs: a real
consumer for `legacy_aliases`, and a gate that can see this drift class.

## Why (b), not (a)

The premise behind wq#4210 is wrong, so there is nothing to deploy. The live gateway
entry reads (keys elided):

```
model_name: deepseek-v4-flash  ->  litellm_params.model: deepseek/deepseek-flash
model_name: deepseek-chat      ->  litellm_params.model: deepseek/deepseek-flash
```

`deepseek-flash` is DeepSeek's **upstream** model id, already the target of the
`deepseek-v4-flash` alias. It is not, and never was, a gateway `model_name` — the name
a caller passes. wq#4210 renamed the fleet-facing id to the provider-facing one. So
option (a) (deploy a second `deepseek-flash` model_name) would add a duplicate alias for
a model the gateway already serves under the name every caller, pool list and tier table
already uses, and would need a hand-merge into the live config, which is outside worker
worktree access anyway (ISSUE-LOG #1216, same constraint as wq#897/948/1295/1406/1590).

Reverting restores one name end to end: `research/models.yaml` id = gateway `model_name`
= the literal in `governor_core._FROZEN_LOCAL_POOL`, `judgment.LOCAL_POOL_MODELS`,
`router.LOCAL_RUNG_POOL_MODELS`, `despag/tiers.TIER_MODELS['mechanical']` and
`despag/harnesses.yaml`. Those four lists needed no edit, which is the point: the
reachable break wq#4217 found (despag `resolve_tier` raising `ModelUnavailable`/422 for
the id the router itself advertised) is gone because the router advertises the served
name again.

## What changed

- `research/models.yaml:234` — `id: deepseek-v4-flash`, `legacy_aliases: [deepseek-flash,
  deepseek-v4-flash-vision-exp]`, and a comment recording the model_name/upstream-id
  distinction so the rename is not re-attempted.
- `orchestrator/router.py:_build_aliases` — reads `legacy_aliases`, one rung below the
  singular `alias` and below any live id, so a caller that picked up `deepseek-flash`
  after wq#4210 still resolves and a legacy claim can never shadow a live name.
- `ops/wq2903-model-ref-drift/model_ref_lint.py` — new `MANIFEST` finding kind: a
  `LIVE*` manifest id on a pool the manifest declares `source: litellm` that the live
  `model_list` does not serve. `DARK` rows and `down` rungs are out of scope by design.

## Probe (can fail, pinned)

Live, both directions, same binary:

```
$ python3 ops/wq2903-model-ref-drift/model_ref_lint.py \
      --models-yaml /home/lumen/foreman/research/models.yaml /home/lumen/bin/model-ref-lint
MANIFEST deepseek-flash   /home/lumen/foreman/research/models.yaml:234      # pre-fix tree
exit=1
$ ... --models-yaml research/models.yaml /home/lumen/bin/model-ref-lint
model-ref-lint: 0 finding(s); live=35 retired_known=34                      # fixed tree
exit=0
```

Full-roots run with the fixed manifest: 10 findings, all pre-existing caller drift
(`isis-qwen3-32b` x7, `mistral-small-latest`, `claude-max-sonnet`, `claude-max-opus`,
`lumen-lfm2.5-2.6b`, `kimik3-0.4b-local`), **zero** MANIFEST — the new check adds no noise.

Unit probes use committed fixtures, not the live manifest:
`tests/fixtures/models_wq4210_drift.yaml` (wq#4210's own shape → exactly one finding)
and `models_clean.yaml` (→ none), plus a synthetic three-way alias collision in
`orchestrator/tests/test_router.py` proving legacy names lose to live ids and declared
aliases. 393 tests pass across `despag/tests orchestrator/tests governor
ops/wq2903-model-ref-drift/tests`.

ISSUE-LOG #1418.
