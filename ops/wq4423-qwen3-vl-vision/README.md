# wq#4423: pull qwen3-vl:8b on Ollama, retarget `lumen/vision` off moondream

Qwen3-VL 8B (~5GB) is massively more capable than the current moondream
deployment (~1.7GB, weak OCR/dense-text) and adds video support moondream
never had. Target use cases: PCB component ID, document OCR, screenshot
analysis, Freedom Bot camera feed, Limpet vision pipeline.

## Wiring already exists — this is a retarget, not a new model

`router_settings.model_group_alias.lumen/vision -> lumen-vision` is already
live (wq#4102, confirmed in
`ops/wq4102-litellm-alias-fallbacks/tests/fixtures/live_router_settings_20261017.yaml`,
a 2026-10-17 snapshot of the real file). The one `model_list` deployment
named `lumen-vision` currently has `litellm_params.model: ollama/vision`
(the moondream tag). Swapping that one field to `ollama/qwen3-vl:8b` is
enough to make `lumen/vision` serve qwen3-vl — no new model_list entry,
alias, or fallback chain needed.

## Two hard walls hit by this BUILD worker (both expected, both documented
## in prior ops dirs — not re-litigated here, just hit again)

1. **`ollama pull` is denied from cron/fworker context.** `guard.sh`
   blocked `lumen-run start -- ollama pull qwen3-vl:8b` outright:
   `BLOCKED — ollama pull denied from cron/fworker context (wq#1297: large
   pulls are manual off-peak only — bridge risk)`. There is no allowed
   rewrite of this command from inside a worker — it is a deliberate
   fleet-window-only operation (kb/ISSUE-LOG.md wq#1986 precedent).
2. **`/home/lumen/agent/litellm_config.yaml` sits outside this worker's
   granted roots.** Confirmed via `cat` on the live path (`This command
   contains multiple operations. The following part requires approval:
   cat /home/lumen/agent/litellm_config.yaml`) — same wall
   `ops/wq4213-litellm-qwen-local-small-group/README.md` and
   `ops/wq4102-litellm-alias-fallbacks/README.md` already hit and solved
   the same way: ship the edit as a reviewable text-surgery script, apply
   it at fleet-window time.

Neither is a Frank judgment call (instinct 3) — they're structural worker
sandbox limits with an already-established escape hatch (write the window
op, land it, run it later with the access this harness deliberately
doesn't grant a worker). Treated the same way here.

## Fix

`swap_vision_model.py` — comment-preserving text surgery on the single
`litellm_params.model` line inside the `lumen-vision` entry. Same safety
pattern as wq#3212/wq#4135/wq#4213: the rendered result is re-parsed and
compared against an independently-built expected plan before any write;
refuses rather than guesses if the entry is missing, duplicated
(ambiguous), or already pointing at a third, unexpected model (config
drift since this was written — see `test_refuses_on_drifted_model_value_outside_from_or_to`).

```
swap_vision_model.py check CONFIG
swap_vision_model.py apply CONFIG --write
```

`probe_vision.py` — real (non-mock) vision completion probe: sends an
actual image as a base64 `image_url` content block to `lumen/vision` and
checks for a non-empty answer, not just a 200. A vision deployment can
return 200 on a text-only prompt while unable to see anything, so this is
the only probe that proves the swap works, not just that the route exists.

```
probe_vision.py --image /path/to/pcb.jpg
```

## Window plan

```
ops/wq4423-qwen3-vl-vision/pull_and_reload.sh IMAGE=/path/to/real-pcb-photo.jpg
```

Pulls `qwen3-vl:8b`, applies the config swap, restarts `litellm` only via
the compose file at `fleet-config/docker/litellm/docker-compose.yml`
(mirrors the live container per that file's own header comment), waits for
`/health/readiness`, then runs `probe_vision.py` against the given image.

**PCB test image**: the ticket asks to test against "a real PCB image from
the Miko dumps." This worker's granted roots
(`worktree, agents/work-queue, mailbot, pokemon, bin, foreman, kb,
sentinel, /tmp/fworker-wq4423`) contain Miko3 *firmware/partition* dumps
(`ops/wq4072-miko3-partition-safety/`, `scratch/research/miko3-partition-safety-map.md`)
but no photo — "Miko dumps" there means eMMC/partition images, not
pictures of the board. No PCB photograph was found inside this sandbox.
Whoever runs the window op needs to supply a real path via `IMAGE=`; if
none exists yet, take one first. The op explicitly logs
"VERIFY SKIPPED: no PCB image path given" rather than silently passing
when `IMAGE` is unset.

## Rollback

Restore the timestamped `.bak-wq4423-<ts>` written next to the config by
`--write`, recreate/restart `litellm`.

## Tests

`python3 -m pytest ops/wq4423-qwen3-vl-vision/tests/` — 10 cases: check
reports needs-apply/ok correctly, the swap touches only the one field,
comments and every other entry survive untouched, the alias relationship
is preserved, idempotency on a second pass, the plan-mismatch refusal, that
`--write` leaves a byte-identical backup, and three falsifiable boundary
cases outside the happy path (wq#2630): a drifted third model value, an
ambiguous duplicate `lumen-vision` entry, and a missing group.
