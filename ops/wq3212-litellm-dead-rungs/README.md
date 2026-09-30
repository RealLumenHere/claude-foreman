# wq#3212 — 5 dead LiteLLM rungs alert forever; watchdog probes only 6

Found by overnight-1 2026-09-30: litellm-completion-probe (wq#3169) had three
hosts failing x20 each (aq 2579 `anthropic`, 2583 `host.docker.internal:8000`,
2585 `host.docker.internal:8091`) while lumen-model-watchdog logged
`probed=6 down=none`. Two defects: dead rungs living in `litellm_config.yaml`
with no removal path, and two monitors deriving DIFFERENT rung lists.

## Per-rung decisions

| rung | decision | why |
|---|---|---|
| `claude-opus-5` | **remove** | provider `anthropic`, key defused **by design** (no API entitlement — council/ISSUE-LOG). wq#3168 kept it as "intentional defusal", but a rung that can never answer now pages the completion probe forever. A by-design-dead rung and a completion probe cannot coexist; the rung goes. |
| `claude-max-sonnet`, `claude-max-opus` | **remove** (not install-the-shim) | They point at claude-shim on `:8091`, which was never installed (ISSUE-LOG wq#2628 2026-09-25, re-confirmed by wq#3168 and this ticket's probes). Installing the shim fronts Frank's claude.ai Max session as an API endpoint — a capability decision, not a tidy-up, and wq#3168 already retired the council `claude` seat (`tools/council/council.yaml` `enabled: false`) days ago with nobody reversing it. Removal continues that landed decision. **Restore path** if the shim is ever wanted: `claude-shim/systemd/INSTALL.md` (units + config entries are all still in-repo, `research/CLAUDE_SHIM_DESIGN.md` §8 has the exact `model_list` shape). |
| `lumen-lfm2.5-2.6b`, `kimik3-0.4b-local` | **remove** | `host.docker.internal:8000` on Lumen is a docker-proxy to an unrelated container bound to `127.0.0.1` — unreachable from the litellm container by construction, and Lumen runs no models by directive. No unit/compose/bind config exists in-repo to restore (wq#3168 checked). |

`messenger-chat` / `messenger-re` stay: NEVER_PROBE (LiteLLM bug #22249), not
dead — just never probed.

## What ships here

- `litellm_rungs.py` — the ONE rung-list derivation (host → eligible chat
  models from `litellm_config.yaml`; NEVER_PROBE and embed-class excluded).
  Deployed to `/home/lumen/bin/litellm_rungs.py`.
- `ops/wq3169-litellm-completion-probe/litellm-completion-probe` (patched) —
  enumerates via the shared module; now also **resolves its own aq row** on a
  recovery edge and when a host leaves the probe set (rung removed from
  config). Before, a removed rung's row stayed open forever.
- `ops/wq2903-model-ref-drift/lumen_model_watchdog.py` (patched) — roster =
  curated CLOUD/SLOW/LOCAL names **∪ first eligible model of every config
  host not covered by a curated name**, from the same shared module and the
  same config file. `down=none` can no longer coexist with a dead config
  rung. Config-derived extras page via aq only (no Gatus push — no
  `6-models_*` endpoint exists for them; aq#352 404-spam lesson). Fail-soft:
  if module/config is unreadable the watchdog runs the curated roster and
  says so (the completion probe hard-fails on the same file, so total config
  loss still pages).
- `remove_dead_rungs.py` — the window op. Text surgery (comments preserved),
  refuses if a removed name is referenced outside `model_list`, validates
  parsed-equality before writing, timestamped `.bak`, resolves the
  emptied-host aq rows (2579/2583/2585 class) on `--apply`.

## Window plan (one recreate — Mon 2026-10-05, wq#1402)

Order inside the window, before `litellm-upgrade --apply`:

```
python3 ops/wq3212-litellm-dead-rungs/remove_dead_rungs.py --check   # gate
python3 ops/wq3212-litellm-dead-rungs/remove_dead_rungs.py --apply   # edit + aq resolve
/home/lumen/bin/maint-ops/litellm-upgrade --apply                    # the ONE recreate
lumen-run --root ops/wq3212-litellm-dead-rungs/install.sh            # monitors
ops/wq3212-litellm-dead-rungs/install.sh --verify
```

The litellm container reads the config only at startup
(`claude-shim/systemd/INSTALL.md`), so `--apply`'s recreate picks up the edit;
no second restart. If the window slips, the dead rungs keep paging truthfully
— nothing here suppresses a live failure.

## aq 2579 / 2583 / 2585

Resolved by `remove_dead_rungs.py --apply` in the window (keys `anthropic`,
`host.docker.internal:8000`, `host.docker.internal:8091`); the patched probe's
left-the-probe-set clear closes them independently on its next 30-min tick if
the script's aq step is skipped. They are NOT resolved before the window: the
rungs are genuinely dead until the config edit lands, and this BUILD sandbox
has no aq access anyway (`/home/lumen/bin/aq` symlinks outside the granted
roots — ISSUE-LOG #298).

## Rollback

- Config: restore the timestamped `.bak` written by `remove_dead_rungs.py`,
  recreate litellm.
- Watchdog: restore `$WD.bak.<ts>_pre_wq3212` (install.sh writes it).
- Probe/module: `git revert` this change, re-run wq#3169's + this install.sh.

## Tests

`python3 -m pytest ops/wq3212-litellm-dead-rungs/tests/ ops/wq3169-litellm-completion-probe/tests/ ops/wq2903-model-ref-drift/tests/`
— includes a can-fail gate (fallback-reference refusal), comment/pricing
preservation, never-probe host exclusion, aq clears on recovery/left-set, the
watchdog roster union, and a pin against wq#2903's committed fixture (an input
authored outside this ticket, wq#2630 probe discipline).
