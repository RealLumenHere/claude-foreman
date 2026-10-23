# wq#4483 -- `lumen/code` routes to OFFLINE isis with no fallback

Found 2026-10-22 by docs-s-b6155b. `/home/lumen/agent/litellm_config.yaml`:
`lumen/code` -> `ollama/qwen3.6:35b-a3b` @ `100.100.129.24` (isis). isis is
hard-off per wq#4420 (fleet-refresh log 2026-10-22; Macs retiring per Frank
2026-10-22). `router_settings.fallbacks` has no `lumen/code` entry, so once
isis is down the raw connector error reaches the caller instead of a
graceful fallback -- same bug shape as wq#4213's `qwen-local-small`.

## What this sandbox could and couldn't verify

Live from this BUILD worktree (2026-10-23, `ops/wq4135-litellm-fast-fallback-retarget/probe_rung.py`
against the running `litellm` on `127.0.0.1:4000`):

- `lumen/code` -- connection closed by the gateway without a response
  (consistent with isis being unreachable upstream). **Confirms the ticket's
  core claim: `lumen/code` is dead right now.**
- `lumen/judge`, `wadjet`, `deepseek-v4-flash` -- all three got
  `ConnectionResetError` on the same probe. That is not a liveness signal
  either way: the completion-probe service key
  (`model-watchdog.key`, read-only via `probe_rung.KEY_PATH`) is scoped for
  watchdog use and also failed on `/v1/models`, `/model/info`, and
  `/spend/logs` with the same reset, so the reset tracks the key's
  permissions, not the models' health. **This sandbox has no key that can
  list `/v1/models` or query SpendLogs**, so the ticket's own research step
  ("SpendLogs last 7d to find real consumers of `lumen/code`") and the
  "verify wadjet with `ollama list` first" step cannot be done from here.
- `/home/lumen/agent/litellm_config.yaml` is outside every directory this
  BUILD worker is granted (not in the worktree, not in `--add-dir`) and is
  explicitly never committed (`fleet-config/docker/litellm/docker-compose.yml:1-4`:
  "credential-bearing... mounted... never baked into an image and never
  committed"). Same wall wq#4213, wq#4135, wq#3212 hit -- a BUILD worker
  cannot read or write it directly. `lumen_state.py current wadjet`
  (`/home/lumen/bin/lumen_state.py`) is in this worker's allowed roots but
  not in its command allowlist, so even host-identity lookups are blocked
  here, not just the config edit.

## Decision

Retarget `lumen/code` onto `deepseek-v4-flash` and add a `lumen/code`
fallback entry pointing at the same model, rather than `wadjet`. Both are
named by the ticket as online candidates, but `wadjet` explicitly needs an
`ollama list` check this sandbox cannot perform, while `deepseek-v4-flash`
is a hosted route already defined in the config with no live-host
dependency to verify. If a fleet operator with `/home/lumen/agent` access
confirms `wadjet` serves a coder-sized model and prefers it as the primary
(keeping `deepseek-v4-flash` as the fallback), that's a one-flag change:
`retarget_lumen_code.py apply CONFIG --target wadjet --write` plus adding
`wadjet` to the fallback list by hand -- the script and its tests don't
need to change.

Not done here, per the ticket's own scoping:
- `lumen/judge` -> hathor fallback is wq#4122, tracked separately.
- Removing the isis/hathor/nephthys/khepri entries waits on "once the Macs
  are formatted" (ticket's own words) -- not yet confirmed done.

## Fix

`retarget_lumen_code.py` (comment-preserving text surgery, same pattern as
wq#4213/wq#4135/wq#3212 -- never a YAML round-trip, which would drop the
file's comments and key order):

```
retarget_lumen_code.py check CONFIG                                   # gate, can fail
retarget_lumen_code.py apply CONFIG --target deepseek-v4-flash --write  # default target
```

`apply` re-parses the rendered result and diffs it against an independently
built expected `litellm_params`/fallback block before anything is
considered successful; `check`'s exit code is non-zero exactly when
`lumen/code` has no deployment or no fallback entry (tested against the
ticket's actual symptom, not a model-only fixture).

## Window plan

This BUILD worker cannot read or write `/home/lumen/agent/litellm_config.yaml`
or touch the `litellm` container (see above). The actual apply is a
fleet-window op:

```
python3 ops/wq4483-litellm-code-fallback/retarget_lumen_code.py check /home/lumen/agent/litellm_config.yaml
python3 ops/wq4483-litellm-code-fallback/retarget_lumen_code.py apply /home/lumen/agent/litellm_config.yaml --write
ops/wq4483-litellm-code-fallback/reload_and_verify.sh
```

`reload_and_verify.sh` probes `deepseek-v4-flash` live first (precheck --
retargeting onto a dead rung would be worse than the current bug), then
restarts `litellm` only if the running gateway doesn't already reflect the
edit, and verifies with `probe_rung.py` that `lumen/code` answers after.
Before running it for real, the operator should also run the ticket's two
unfinished research steps with full access: a SpendLogs query for
`lumen/code`'s last-7d consumers (to catch anything relying on a
`qwen3.6:35b-a3b`-specific behavior deepseek-v4-flash doesn't share), and
`ollama list` on wadjet (to settle the wadjet-vs-deepseek-v4-flash choice
above with real data instead of this sandbox's elimination-by-default).

## Rollback

Restore the timestamped `.bak-wq4483-<ts>` written next to the config by
`--write`, recreate/restart `litellm`.

## Tests

`python3 -m pytest ops/wq4483-litellm-code-fallback/tests/` -- 8 cases: the
can-fail gate on the ticket's actual broken shape (dead deployment + no
fallback), that `check` passes once fixed, that only `lumen/code` is
retargeted (the sibling `isis-coder-14b` deployment and its stale
description survive untouched -- that's wq#4228's separate finding, not
this ticket's), that the pre-existing `lumen-lfm2-1b` fallback survives,
idempotency on a second `apply`, a byte-identical backup, no mutation
without `--write`, and the plan-mismatch abort on an unknown `--target`.
