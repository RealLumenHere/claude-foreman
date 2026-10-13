# wq#3797 — verify: does wq#3796 correctly restore hathor's prefill_tps?

**Verdict: CONFIRM.** PR claude-foreman#1794 (commit `0b2638c4`, merged to `main` via merge
commit `befbd8ca`) implements exactly the opus-5@high judgment's recommendation #1/#2 from
`reports/3795_judgment_wq3794_hathor_prefill_tps_premature_clear.md`, and independent probes
show the regression it targets is actually fixed, live.

Verifier: claude-sonnet-5@high (fresh instance, same model as author claude-sonnet-5). No
contradiction found, so no escalation to opus-5@high. Read-only except for a local scratch
branch (`pr-1794-review`, fetched from `refs/pull/1794/head`) used to inspect the PR; nothing
pushed, no `wq`/ssh mutations.

## What the PR claims

Restore `prefill_tps: 13.6` and `blob: "llama3.1:8b"` for `hathor-llama8b` in
`research/inference_nodes.yaml`, re-render `dispatcher/config.json` and
`ops/inference-registry/litellm_local_block.yaml`, and revert the two test assertions that had
followed wq#3794's premature clear — undoing the fail-open regression wq#3795 found (hathor's
`prompt_over_budget()` guard silently stopped refusing oversized prompts once `prefill_tps` went
null).

## What I checked

1. **Diff matches the judgment's recommendation exactly.** `git show 0b2638c4` (full diff read,
   110 lines): `research/inference_nodes.yaml:31,69-71` restores `blob: "llama3.1:8b"` and
   `prefill_tps: 13.6` with a comment citing wq#3795/wq#3796 and the actual reason (no live
   pull/retag on hathor); `first_frame_s: 1500` is left bare (no longer tagged "carried over from
   Q4_K_M" — correct, since the blob didn't change). `dispatcher/config.json:9` gets
   `"prefill_tps": 13.6` back on the `hathor-llama8b` row. `ops/inference-registry/litellm_local_block.yaml:3,10`
   reverts `model`/`description` to `llama3.1:8b`. `harness/tests/test_inference_registry.py:51,55`
   and `dispatcher/tests/test_node_render_gateway.py:28` revert the hathor tuple and blob
   assertions to match. isis/qwen36-local rows are untouched (confirmed: no isis/qwen36 lines
   appear anywhere in the diff) — correct per the judgment, since isis is `state: down`.

2. **Generated targets aren't hand-edited, they're real renders.** Ran both renderers against the
   merged tree in `--check` mode, which exits nonzero on drift between the registry and its
   generated targets:
   - `python3 harness/fleet_node.py render --check` → `0 target(s) drifted`, exit 0.
   - `python3 harness/node_render_gateway.py render --check` → prints the model list, exit 0.
   Both confirm `dispatcher/config.json` and `ops/inference-registry/litellm_local_block.yaml`
   are byte-consistent with `research/inference_nodes.yaml` as committed — the PR's description
   of running the renderers is verifiably true, not just claimed.

3. **Tests pass.** `python3 -m pytest harness/tests/test_inference_registry.py
   dispatcher/tests/test_node_render_gateway.py -q` → 63 passed.
   `python3 -m pytest harness/tests/test_local_cli_profile.py -q` → 33 passed (this is the file
   wq#3795 flagged as silently skipping rows with no `prefill_tps`; it now exercises hathor's row
   again since the row has a rate).

4. **The actual guard mechanism is re-armed — a falsifiable probe, not just config review.**
   Built `LocalCapacity` from the live merged `dispatcher/config.json` and called
   `spawn_env('hathor-llama8b')` directly:
   `{'FOREMAN_FIRST_FRAME_S': '1500', 'FOREMAN_SERVED_CTX': '32768', 'FOREMAN_PREFILL_TPS': '13.6'}`
   — the env var wq#3795 showed goes missing is back. Fed that env into
   `harness/charging.prompt_over_budget` for two prompt sizes (the probe can fail in both
   directions, satisfying wq#2630):
   - 15,000 tokens (ordinary fworker prompt size, `models.yaml:305` puts full ones at 15-22k):
     `"prompt ~15000 tokens / prefill 13.6 tok/s x2 (shared box) = ~2206s exceeds the 1500s
     first-frame budget"` — refused, as it was before wq#3794's regression.
   - 5,000 tokens: `None` — admitted, proving the probe isn't vacuously refusing everything.
   Before this PR (prefill_tps null), both calls returned `None` (fail-open) per wq#3795's trace;
   this reproduces that the fix, not just the config diff, closes the gap.

5. **Live confirmation, not just repo state.** `/home/lumen/foreman/dispatcher.log`'s latest
   `pass_start` (2026-10-13T23:44:17Z) reads `"config_sha": "befbd8ca...", "config_blob":
   "7b1a6f03..."` — exactly the merge commit and the blob hash this diff produced
   (`git rev-parse 0b2638c4:dispatcher/config.json` = `7b1a6f032a3f5899fc96c43c55ff4a6d219583ff`).
   The running dispatcher has already loaded the corrected config; this isn't a fix that's merged
   but undeployed.

6. **No collateral damage found.** `judge/JUDGE_MODEL_IDENTITY.json` (untouched by this PR) still
   pins `tag: "llama3.1:8b"` with the Q4_K_M digest, consistent with the reverted blob — no new
   mismatch introduced. Grepped the repo for `q8_0`/`Q8_0` outside this PR's own files and found
   only unrelated KV-cache-quant tooling (`tools/macbench/*`, `pilots/dab847127347/*`, which test
   `OLLAMA_KV_CACHE_TYPE`, a different axis) — nothing else references the abandoned Q8_0 blob
   swap as if it were live.

## Minor note (not a defect, not blocking)

`research/WQ3794_MAC_FLEET_RECONFIG.md:17-18,52` still describes the original wq#3794 plan
("blob bumped ... to llama3.1:8b-instruct-q8_0", "prefill_tps cleared") and wasn't touched by this
PR. That's fine as a historical record of what wq#3794 intended/did; the registry itself
(`inference_nodes.yaml:69-71`) carries the authoritative, corrected comment for current state. If
someone reads only the planning doc without the registry they'd get a stale picture, but nothing
in code or config depends on that doc, so this is cosmetic.

## What I did not check

- Hathor's live `/api/tags` (ssh/network out of scope for a repo-diff verify, same boundary
  wq#3795 drew).
- Rotated `dispatcher.log.*.gz` history predating 2026-10-11.
- The broader `harness/tests/` + `dispatcher/tests/` suites beyond the files this PR touches and
  `test_local_cli_profile.py` were kicked off in the background for completeness; nothing in the
  diff suggests they'd be affected, and no interim failures were observed before this report was
  filed.

## Conclusion

wq#3796 does what wq#3795's judgment asked: restores `prefill_tps: 13.6`, keeps the Q8_0 swap as
a documented plan rather than a deployed fact, re-renders (not hand-edits) the generated targets,
and the restored guard provably refuses the class of prompt that was slipping through. No
contradiction found; confirming on the board, no escalation.
