## What

wq#3212. Retire the 5 permanently-dead LiteLLM rungs (`claude-opus-5`,
`claude-max-sonnet`, `claude-max-opus`, `lumen-lfm2.5-2.6b`,
`kimik3-0.4b-local`) via a window op that rides the wq#1402 recreate, and make
lumen-model-watchdog and litellm-completion-probe derive their rung lists from
one shared module reading the same `litellm_config.yaml`.

## Why

litellm-completion-probe (wq#3169) had three hosts failing x20 (aq 2579
`anthropic`, 2583 `host.docker.internal:8000`, 2585 `host.docker.internal:8091`)
while the watchdog logged `probed=6 down=none` — the monitors disagreed because
the watchdog probes a hardcoded 6-name roster while the probe enumerates config
hosts. The dead rungs also had no removal path and their aq rows no close path.

Per-rung decisions (full reasoning in `ops/wq3212-litellm-dead-rungs/README.md`):
`claude-opus-5` removed — anthropic key defused by design, can never answer a
completion probe. `claude-max-*` removed rather than installing claude-shim —
the shim was never installed (ISSUE-LOG wq#2628, wq#3168) and wq#3168 already
retired the council `claude` seat; the restore path stays documented in
`claude-shim/systemd/INSTALL.md`. `lumen-lfm2.5-2.6b`/`kimik3-0.4b-local`
removed — `:8000` proxies to an unrelated `127.0.0.1`-bound container and Lumen
runs no models by directive.

## How

- `ops/wq3212-litellm-dead-rungs/remove_dead_rungs.py` — window op, runs before
  `litellm-upgrade --apply` on Mon 2026-10-05 so there is exactly one recreate.
  Comment-preserving text surgery; refuses if a removed name is referenced
  outside `model_list`; parsed-equality validation before writing; timestamped
  backup; resolves aq 2579/2583/2585 (the emptied-host keys) on `--apply`.
- `ops/wq3212-litellm-dead-rungs/litellm_rungs.py` — single host/rung
  derivation for both monitors. NEVER_PROBE (LiteLLM bug #22249) enforced at
  eligibility so a never-probe model can never be elected host representative.
- Watchdog (`ops/wq2903-model-ref-drift/lumen_model_watchdog.py`) — roster =
  curated names ∪ first eligible model of every uncovered config host;
  config-derived extras page via aq only (no Gatus endpoint exists for them,
  aq#352). Fail-soft to the curated roster if module/config is unreadable.
- Probe (`ops/wq3169-litellm-completion-probe/litellm-completion-probe`) — uses
  the shared module; auto-resolves its aq row on a recovery edge and when a
  host leaves the probe set (previously such rows stayed open forever).
- `ops/wq3212-litellm-dead-rungs/install.sh` — deploys module + both monitors
  in the window; `--verify` mode per the wq#1696 contract.

## Testing

27/27 pass: `python3 -m pytest ops/wq3212-litellm-dead-rungs/tests/
ops/wq3169-litellm-completion-probe/tests/ ops/wq2903-model-ref-drift/tests/`.
Can-fail gates included: fallback-reference refusal (script must not write),
never-probe host exclusion, comment/pricing preservation, aq clears on
recovery/left-set, watchdog roster union, and a pin against wq#2903's
committed fixture — an input authored outside this ticket (wq#2630 discipline).

## Risk / rollback

Nothing deploys until the window: the live config, probe, and watchdog are
untouched by merge alone. In-window rollback: restore the timestamped config
`.bak` written by `remove_dead_rungs.py` and recreate litellm; watchdog restores
from `*.bak.<ts>_pre_wq3212` (install.sh writes it); probe/module revert via
git + reinstall. If the window slips, the dead rungs keep paging truthfully —
nothing here suppresses a live failure.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
