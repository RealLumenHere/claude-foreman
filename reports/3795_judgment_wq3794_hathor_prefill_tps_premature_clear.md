# wq#3795 — judgment: did wq#3794 clear hathor's prefill_tps on a false "stale" premise?

**Verdict: UPHELD.** The "stale at the old quant" premise is false for hathor, and the edit turns
off wq#2185's prefill guard for a `state: live` backend that still has a dispatcher row. The
verifier overstates the blast radius, though. No routing chain sends fworker work to hathor today,
so the damage is latent. It only bites on an explicit `hathor-llama8b` / `local-worker` pin.

Judge: claude-opus-5@high, escalation for wq#3795 (verify-pass on PR claude-foreman#1792, commit
`0644fa88` on `main`). Read-only: no code edited, no git state touched, no ssh, no `wq` commands.
Every line reference below is from `/home/lumen/foreman` at `0644fa88`.

## Ruling 1 — the premise is false for hathor (UPHELD)

- `research/inference_nodes.yaml:50`: hathor is `state: live`. The row at `:69` now reads
  `prefill_tps: null  # wq#3794: stale at old quant (was 13.6), cleared pending re-bench at Q8_0`.
- `judge/JUDGE_MODEL_IDENTITY.json:3-7`: `hathor-llama8b` still pins `tag: "llama3.1:8b"` with
  the Q4_K_M digest `667b0c19…`, which this PR left unchanged.
- The PR's own doc, `research/WQ3794_MAC_FLEET_RECONFIG.md:51-53`, says the pin was left alone
  "because hathor is not actually serving that tag yet (… no live Ollama pull/retag happened on
  hathor)". At `:17-19` the same doc calls 13.6 "stale at the old quant".

These two statements contradict each other. 13.6 tok/s (measured 2026-09-20) describes the old
quant, and hathor still serves the old quant. The number goes stale when the Q8_0 pull happens,
and this PR says that hasn't happened yet. The PR got the identity pin right (`:46-59`, "never
point at a tag the node doesn't yet serve") but did not apply the same reasoning to the
dispatcher row, which also describes what hathor serves.

For isis, I agree with the verifier that clearing is harmless. `inference_nodes.yaml:113` has
`state: down` (wq#2140), and `research/models.yaml:330` has `status: down`.

## Ruling 2 — the mechanism is real and live (UPHELD)

I traced each step myself:

1. **The dispatcher reads the post-merge row live.** The last `pass_start` in
   `/home/lumen/foreman/dispatcher.log` (2026-10-13T23:30:39Z) logs
   `config_path: /home/lumen/foreman/dispatcher/config.json`, `config_sha: 0644fa88…`,
   `config_blob: a387a026…`. That blob equals `git rev-parse 0644fa88:dispatcher/config.json`.
   `dispatcher/config.json:9` now reads
   `"hathor-llama8b": {…, "first_frame_s": 1500}` with no `prefill_tps`.
2. **The env var is no longer emitted.** `dispatcher/local_capacity.py:259-261` only sets
   `FOREMAN_PREFILL_TPS` when the row has a positive number. `foreman_dispatcher.py:2007-2010`
   passes `cap.spawn_env(member or model)` into `spawner.dispatch_one(..., env=...)` for every
   local-partition spawn.
3. **The guard fails open.** `harness/charging.py:325-332`: `prefill_tps()` returns `None`
   when the var is missing. `:341-347`: `prompt_over_budget()` returns `None` (allow) when
   `tps is None`. `harness/tests/test_local_cli_profile.py:186-190` asserts this fail-open
   behaviour on purpose, so the guard is meant to stay silent without a rate.
4. **The call site is real.** `harness/fworker.py:3695-3698` runs it for every
   `is_local_rung(args.model)` spawn and raises `SpawnRefused("PROMPT_OVER_BUDGET")`.
   `hathor-llama8b` is a local rung (`fworker.py:229-230`, from `MAC_POOL_MODELS`).
5. **The check is not moot.** `first_frame_s: 1500` is still set (`config.json:9`), so the
   guard's only missing input is the rate.
6. **Hathor is still selectable.** wq#2796 excluded hathor from the *gateway's* `local-worker`
   group (`local_worker_group: false`, `inference_nodes.yaml:73-78`). The same comment says this
   "affects target 1 (gateway model_list) only". The dispatcher's group still lists it
   (`config.json:12`, `"local-worker": {"group": ["hathor-llama8b", …]}`), and
   `local_capacity.pick_member()` (`:221-238`) can choose it. A direct `hathor-llama8b` pin is also
   valid, since hathor is `LIVE-local` in `research/models.yaml:315-316`.

**What changed, numerically.** Before the PR, hathor refused any prompt over
13.6 × 1500 / 2 ≈ **10,200 tokens** (`SHARED_BOX_FACTOR = 2`, `charging.py:57`). Now the only
size gate is `prompt_over_ctx`, which allows 0.6 × 32768 ≈ **19,660 tokens** (`charging.py:46`).
Prompts of roughly 10.2k–19.7k tokens used to be refused uncharged and are now admitted. That
band covers ordinary fworker prompts: `models.yaml:305` puts full ones at 15-22k. At the measured
rate, a 19.6k prompt on a shared box needs about 2,890 s to prefill, against a 1,500 s budget.

**A second effect the verifier missed.** `fworker.py:3921` also passes
`charging.prefill_tps()` into the charge classifier. `charging.py:206-209` uses
`sent_too_much()` to bill a first-frame kill of an oversized prompt to the harness
(`prompt_over_budget`). With no rate, that branch can't fire. The kill falls through to `:217-219`
and is billed to the rung as `first_frame_timeout`, which puts `hathor-llama8b` into
`runtime/backend_cooldown.json`. That file already holds one such strike from 2026-09-23. So the
PR removes the up-front refusal and also changes who gets blamed afterwards: the healthy judge
box takes the strike for a prompt the harness should never have sent it.

## Ruling 3 — the blast radius is narrower than claimed (verifier's severity CORRECTED)

The verifier describes this as hitting "hathor's CURRENT … workload". That overstates it:

- **No routing chain reaches hathor.** `research/models.yaml:362-369`: wq#2583 dropped
  `local-worker` from every chain after 0/166 local fworker sessions succeeded. `:369-374` lists
  only cloud rungs, and `local_standard_engineering: false` (`:399`).
- **No local spawns in the current log.** `dispatcher.log`, which runs from 2026-10-11 to now,
  has 63 `spawn` events. All are `claude-*`. No line mentions `local-worker`,
  `nephthys-qwen14b` or `qwen36-local`, and the only `hathor` mentions are `ssh(hathor)`
  capability tags. I could not read the rotated `.gz` logs (the sandbox wanted approval for
  `zcat`), so older history is unchecked.
- **Hathor's real workload is untouched.** That workload is `bin/judge`, which talks to Ollama
  directly. Nothing under `judge/` or `bin/` reads `prefill_tps` or `prompt_over_budget`.

So nothing is going wrong today. The guard is disarmed for a live backend that can still be
reached three ways: an explicit `--model hathor-llama8b` pin, a `--model local-worker` pin that
`pick_member` resolves to hathor, or a future re-entry of `local-worker` into a chain under the
wq#2583 rule. In any of those cases, a 10-20k-token prompt starts instead of being refused. It
holds one of hathor's 2 Ollama slots, contending with synchronous judge calls (the contention
wq#2796 exists to prevent), for 1,500 s until the first-frame kill. Then it puts hathor in
cooldown. This is a fail-open defect with low current probability. It is not a crash or data
loss, but it is a real invariant violation and should be fixed.

## Counter-evidence I looked for and did not find

- **Another path that re-enables the check.** None. `FOREMAN_PREFILL_TPS` is only written in
  `local_capacity.py` and only read in `charging.py`, outside tests and a stale
  `.claude/worktrees/` copy.
- **A null/live precedent.** The precedent points the other way.
  `harness/node_lifecycle.py:345-358` (`fleet-node add`) writes `prefill_tps: None` only together
  with `state: "down"`. `record_bench` (`:378-387`) sets the measured rate and `state: live` in
  one step. In this codebase, "unmeasured" goes with "not live". The PR broke that pairing for
  hathor.
- **Tests that would catch it.** None do. The PR's test edits check that the data matches what
  was written: `test_inference_registry.py` changes `13.6` to `None`, and
  `test_node_render_gateway.py` renames model strings. Neither looks at spawn behaviour.
  `test_local_cli_profile.py:193-200` loops over config rows and skips any row without
  `prefill_tps` (`if … row.get("prefill_tps")`), so hathor left that test's coverage silently
  instead of failing it.

## Recommendation (not implemented, per protocol)

1. **Restore hathor's rate for now.** In `research/inference_nodes.yaml:69`, set
   `prefill_tps: 13.6` and add a comment like "Q4_K_M, still served; replace after the Q8_0
   pull + `fleet-node bench`". Re-render with `fleet_node.py render --apply` so
   `dispatcher/config.json:9` gets `"prefill_tps": 13.6` back; don't hand-edit the generated
   block. Revert the hathor tuple in `harness/tests/test_inference_registry.py` to
   `13.6` in the same commit.
2. **Separate plan from deploy.** The hathor `blob:` bump has the same root cause. It also
   re-rendered `ops/inference-registry/litellm_local_block.yaml` to
   `model: openai/llama3.1:8b-instruct-q8_0`, a tag hathor does not serve. Nothing is broken
   while that block is only rendered (the doc says `apply` was not run), but the next gateway
   apply from `main` would point `hathor-llama8b` at a missing model. The cleanest fix is to keep
   the hathor blob at `llama3.1:8b` in the registry and record Q8_0 as a planned change in
   `WQ3794_MAC_FLEET_RECONFIG.md`. Then do the whole hathor change in one ops pass: pull Q8_0,
   bench it (writes `prefill_tps`/`first_frame_s` via `record_bench`), update the blob, then
   update `JUDGE_MODEL_IDENTITY.json` tag and digest. That is the ordering the doc already uses
   for the identity pin at `:55-58`.
3. **Leave isis alone.** `state: down` makes the cleared numbers inert.
4. **Optional hardening, separate ticket.** A registry lint stating "`state: live` ⇒ every
   resident model has a non-null `prefill_tps`" would have caught this. `node_lifecycle.py`
   already follows that rule. If such a lint is added, have
   `test_a_calibrated_mechanical_edit_prompt_clears_every_configured_backend` fail on a live row
   with no rate instead of skipping it.

Route this as a follow-up fix ticket on wq#3794's branch work. Reverting the whole PR isn't
needed: the isis changes, the deferred-work notes and the identity-pin decision are correct.

## What I checked myself

- `git show 0644fa88` for `dispatcher/config.json`, `research/inference_nodes.yaml`, both test
  files and `ops/inference-registry/litellm_local_block.yaml`.
- Read in full: `research/inference_nodes.yaml`, `WQ3794_MAC_FLEET_RECONFIG.md`,
  `dispatcher/local_capacity.py`.
- Read the relevant ranges of: `charging.py:108-114, 185-219, 310-359`;
  `fworker.py:229-249, 3655-3709, 3921`; `foreman_dispatcher.py:1994-2023`;
  `node_lifecycle.py:345-387`; `research/models.yaml:300-399`;
  `test_local_cli_profile.py:174-218`.
- Runtime state: `JUDGE_MODEL_IDENTITY.json:3-7`, `dispatcher/runtime/backend_cooldown.json`,
  and `dispatcher.log` (live config blob, spawn models, local-model mentions).
- Not checked: rotated `dispatcher.log.*.gz`; hathor's live `/api/tags` (ssh/network is out of
  scope). I took "still serves Q4_K_M" from the PR's own statement plus the unchanged identity
  pin. `bin/judge` fails closed on a digest mismatch, so any silent retag would already show up as
  judge failures.
