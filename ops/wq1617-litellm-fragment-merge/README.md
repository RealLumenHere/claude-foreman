# wq#1617 — stop staged full-file LiteLLM config copies from clobbering pricing

`/home/lumen/agent/litellm_config.yaml.staged.minimax-m3` (written 2026-07-31
05:14 UTC) is a full-file copy of the live LiteLLM proxy config. It has zero
`input_cost_per_token` entries. The live config gained glm-5.3-flash pricing
at 05:31 UTC 2026-09-10 (wq#1591) — anyone who applied that staged file
wholesale would have silently reverted it, sending the fleet's largest
workload back to logging $0.00 spend with no error. A second stale copy,
`.staged.wq233` (2026-07-16), has the same problem. `minimax-m3` itself
never landed live.

This is a class problem, not a one-off: a full-file staged copy is a
lost-update hazard by construction — it can only ever be as current as the
moment it was taken, and nothing stops it from being applied after the live
file has moved on. The fix is the same one wq#1591's fworker already used
ad hoc (`ops/wq1591-glm53flash-pricing/`, PR 390) — merge fragments, not full
files — made into an enforced tool instead of a convention.

## What's here

| File | Purpose |
|---|---|
| `merge_litellm_fragment.py` | `check <live> <candidate>`: refuses (exit 1) if applying `candidate` over `live` wholesale would drop any pricing key (`input_cost_per_token`/`output_cost_per_token`/`cache_read_input_token_cost`) an existing model already has. `merge <live> <fragment> [-o out]`: overlays a fragment's `model_info` onto the matching live entries (adds a new entry if the model doesn't exist yet), and refuses instead of writing if the fragment would explicitly null out a pricing key live already has. |
| `tests/test_merge_litellm_fragment.py` | Unit tests against synthetic fixtures, including the exact wq#1617 shape (a stale full-file copy predating wq#1591's pricing). No live config or PyYAML-backed file needed. |
| `fragments/minimax-m3.yaml` | The one candidate worth keeping from `.staged.minimax-m3` before it's archived — extracted, not lost. |
| `install.sh` | Quarantines every `litellm_config.yaml.staged.*` file on the gateway node to `agent/staged-archive/<name>.STALE-<date>` with a pointer README, then prints the merge/verify checklist for `fragments/minimax-m3.yaml`. |

## Why the live config isn't touched directly

Same convention as wq#897/wq#1299/wq#1406/wq#1591 (see their READMEs): this
BUILD worker's sandbox has no read or write access to `/home/lumen/agent`
(policy-refused — `agent/.env` holds secrets in that directory). Read-only
snapshots of the live config and both staged files were provided out of
band at `/tmp/fworker-wq1617/inputs/` for this ticket only; the tool and its
tests were verified against those snapshots (see Verification below) but
ship as fragments/scripts for a session with real access to run on the
gateway node, exactly like `install.sh` says.

## Verification performed in this sandbox

```
python3 ops/wq1617-litellm-fragment-merge/tests/test_merge_litellm_fragment.py -v
# Ran 11 tests in 0.025s — OK

python3 ops/wq1617-litellm-fragment-merge/merge_litellm_fragment.py check \
    /tmp/fworker-wq1617/inputs/litellm_config.yaml \
    /tmp/fworker-wq1617/inputs/litellm_config.yaml.staged.minimax-m3
# REFUSED: would drop glm-5.3-flash / glm-5.3-flash-zai pricing
# (confirms the exact hazard this ticket reports, against the real files)

python3 ops/wq1617-litellm-fragment-merge/merge_litellm_fragment.py check \
    /tmp/fworker-wq1617/inputs/litellm_config.yaml \
    /tmp/fworker-wq1617/inputs/litellm_config.yaml.staged.wq233
# REFUSED: same two models — the second stale copy has the identical hazard

python3 ops/wq1617-litellm-fragment-merge/merge_litellm_fragment.py merge \
    /tmp/fworker-wq1617/inputs/litellm_config.yaml \
    ops/wq1617-litellm-fragment-merge/fragments/minimax-m3.yaml \
    -o /tmp/merged_dryrun.yaml
# wrote merged config to /tmp/merged_dryrun.yaml (37 -> 38 model_list entries)

python3 ops/wq1617-litellm-fragment-merge/merge_litellm_fragment.py check \
    /tmp/fworker-wq1617/inputs/litellm_config.yaml /tmp/merged_dryrun.yaml
# OK: does not drop any pricing key the live snapshot currently has
```

## Deploy checklist (for whoever runs this on the gateway node)

1. `install.sh` — quarantines both stale `.staged.*` files to
   `agent/staged-archive/`, so neither can be applied by accident again.
2. Verify the pricing comment in `fragments/minimax-m3.yaml` against
   OpenRouter's current price list — it's carried forward from a 2026-07-31
   evaluation note, not re-checked (same promo-vs-list-rate lesson wq#1591
   caught for glm-5.3-flash).
3. `merge_litellm_fragment.py merge /home/lumen/agent/litellm_config.yaml
   fragments/minimax-m3.yaml -o /tmp/litellm_config.yaml.merged` — or skip
   this step entirely if minimax-m3 isn't wanted; nothing else depends on it.
4. `merge_litellm_fragment.py check /home/lumen/agent/litellm_config.yaml
   /tmp/litellm_config.yaml.merged` — must print `OK` before proceeding.
5. Review the diff, replace the live file, reload the proxy.
6. **Going forward**: run `merge_litellm_fragment.py check <live> <candidate>`
   before any `cp <candidate> <live>` on this file, staged or otherwise —
   that's the actual fix for the class problem, not just this instance.

## Rollback

Nothing in this PR touches the live config or the gateway node — it ships a
guard tool, its tests, and an extracted fragment. Rollback is "don't run
`install.sh` / don't merge `fragments/minimax-m3.yaml`."
