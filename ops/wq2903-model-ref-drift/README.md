# wq#2903: callers of the retired mistral-small rung, and a lint for the class

`mistral-small` left `/home/lumen/agent/litellm_config.yaml` on 2026-08-30
(`.bak.20260830_124308_pre_mistral_drop`). Three callers kept calling it: 1643
ProxyModelNotFoundError in 5 days (fleet-audit-2, LiteLLM_SpendLogs).

| key alias | 5d | caller | fix |
|---|---|---|---|
| model-watchdog | 1405 | `agents/model-watchdog/lumen_model_watchdog.py` `CLOUD` roster, 2 probes per 10-min run | name removed; roster now checked against `GET /v1/models` every run |
| lumen-agent-2 | 198 | `lumen-fleet-mistral.service` → `agent/lumen_quota_manager.py` `PROVIDER_TO_MODEL["mistral"]` | → `deepseek-v4-flash` (FLEET_COST_PLAN.md §4 names it as the lumen-agent consolidation rung) |
| ops-emergency-3 | 40 | `minerva/model_audit.py` `AUDIT_MODELS` default, 4 prompts × 2 runs/day | name dropped from the default battery |

The class: model names hardcoded in callers, nothing comparing them to the live
model_list (same shape as wq#2565). Two guards now:

1. **Runtime, model-watchdog.** A rostered name missing from `/v1/models` is config
   drift: one aq row (`model-watchdog` / `model-roster-drift`), raised when the set
   changes, resolved when it empties, not probed and never pushed to Gatus as DOWN. If
   `/v1/models` can't be read, the full roster is probed as before. On first run after
   deploy, the open `model-mistral-small` DOWN row is resolved ("left the probe set").
2. **Static, `model-ref-lint`** (`/home/lumen/bin/model-ref-lint`, daily timer, `--aq`
   → `model-ref-lint` / `model-ref-drift`). Scans bin, agents, foreman, services,
   minerva, agent and systemd unit dirs:
   - `RETIRED`: any quoted literal (or comma-list item) equal to a hyphenated name that
     some `litellm_config.yaml.bak*` carried and the live list lacks.
   - `UNKNOWN`: a name in model context (`"model": "x"`, `model="x"`, `*_MODEL*=x`,
     `--model x`) in neither the live list nor `foreman/research/models.yaml`.
     Provider-native ids (`a/b`, `name:tag`) and `claude-*` are skipped.
   Comments, Python docstrings, `.bak*`, `state.json`, and docs/reports/ops/tests/backup
   trees are skipped. Provider-direct names (Groq, Cerebras, Ollama) sit in `ALLOW`,
   one reason each; `--allow-file` adds more.

Pre-deploy run on the box, 2026-09-28: exactly the three callers above plus one
retired-registry tuple (now in `ALLOW`):

```
RETIRED  mistral-small  /home/lumen/agents/model-watchdog/lumen_model_watchdog.py:53
RETIRED  mistral-small  /home/lumen/minerva/model_audit.py:27
RETIRED  mistral-small  /home/lumen/agent/lumen_quota_manager.py:225
```

Limit: the lint sees literals only. `wq2821-judge-llama8b` (wq#2904, minerva-bench-2)
matched no file under the scanned roots on 2026-09-28, so that caller builds or reads
the name at run time; `tests/fixtures/callers/pre_fix.py` proves the lint flags the
literal form.

## Deploy

```
ssh lumen@<lumen> 'lumen-run start --root --name wq2903 -- /home/lumen/foreman/ops/wq2903-model-ref-drift/install.sh'
ssh lumen@<lumen> '/home/lumen/foreman/ops/wq2903-model-ref-drift/install.sh --verify'
```

`install.sh` patches the two callers (`patch_callers.py`: exact-string, backs up to
`.bak.<stamp>_wq2903`, aborts untouched if either file changed), installs the watchdog
and lint atomically, enables the timer, restarts `lumen-fleet-mistral` (it imports
`PROVIDER_TO_MODEL` once), runs both once, then runs `--verify`.

## Rollback

- watchdog: `cp lumen_model_watchdog.py.bak.<stamp>_pre_wq2903 lumen_model_watchdog.py`
- callers: restore `<file>.bak.<stamp>_wq2903`, then `systemctl restart lumen-fleet-mistral`
- lint: `systemctl disable --now lumen-model-ref-lint.timer`, remove the two units and
  `/home/lumen/bin/model-ref-lint`

## Tests

`python3 -m pytest -q ops/wq2903-model-ref-drift/tests` (15): lint vs committed fixtures
(the verbatim pre-fix caller lines must all be flagged, look-alikes must not), watchdog
drift/DOWN separation and row lifecycle, patcher idempotence and abort-on-unknown.
