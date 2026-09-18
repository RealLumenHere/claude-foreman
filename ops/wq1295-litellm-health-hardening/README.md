# wq#1295 — gateway hardening: LiteLLM health checks + failure_callback -> ntfy (capped)

Day-0 gateway item from kb/tooling-findings-review.md §5 and
kb/fleet-upgrade-plan-review.md §4.3: LiteLLM already ships most of what the
"brainless LPP" failure class needed (`/health`, `background_health_checks`,
`/health/readiness`) — nobody had turned it on or wired it to an alert.

Gatus's side of this (`/health/readiness` polled at 1m, `/health` at 15m,
`send-on-resolved` alerts) already landed in `ops/wq1184-gatus-health-checks/`
— see that ticket's `config.yaml` lines 56-87. This ticket is the other
half: the gateway-side settings those endpoints depend on, plus the
alerting path for background health-check failures specifically (Gatus
alerts when the *endpoint* fails; this alerts when a *deployment inside*
LiteLLM's own health poll fails, which `/health/readiness` alone can't see
since readiness stays 200 even if one backend model is down).

## Components

| File | Deploy target | Purpose |
|---|---|---|
| `litellm_config_snippet.yaml` | merge into the live proxy `config.yaml` | turns on `background_health_checks`, sets a generous interval/timeout, caps the polled model set, registers the failure_callback |
| `health_check_model_filter.py` | onto the LiteLLM proxy's PYTHONPATH (or run standalone at deploy time) | pure exclusion logic: keeps expensive (opus-class) and local/ollama rungs OUT of the background poll — see module docstring for why each is excluded |
| `health_check_failure_alert.py` | onto the LiteLLM proxy's PYTHONPATH, registered as a `failure_callback` | fires ntfy (edge-triggered, 30min cooldown per model) the moment a health check — or any call — fails for a deployment |
| `backup_and_verify.sh` | run on the gateway node before/after merging | `backup_and_verify.sh backup <config.yaml>` timestamps a copy before editing; `backup_and_verify.sh verify` curls `/health/readiness` after restart and prints a rollback command on failure |
| `tests/` | — | unit tests for every pure decision function, run standalone |

## Why the live config isn't touched directly

Same convention as wq#897/wq#911/wq#948/wq#1299/wq#1406/wq#1590 (see their
READMEs): this BUILD worker's sandbox has no read/write access to the
gateway node's `/home/lumen/agent/litellm_config.yaml`, so this delivers
fragments + scripts to hand-merge and run, not a live change.

## Deploy checklist (operator, on the gateway node)

1. `backup_and_verify.sh backup /home/lumen/agent/litellm_config.yaml`
2. `python3 health_check_model_filter.py /home/lumen/agent/litellm_config.yaml`
   — capture the printed list, replace the `health_check_models` placeholder
   in `litellm_config_snippet.yaml` with it.
3. Merge `litellm_config_snippet.yaml`'s `general_settings`/`litellm_settings`/
   `environment_variables` keys into the live config (append to existing
   lists — `failure_callback` is a list, don't replace wq#911's or any other
   registered callback).
4. Deploy `health_check_failure_alert.py` next to the config (PYTHONPATH);
   restart the proxy.
5. `backup_and_verify.sh verify` — confirms `/health/readiness` still
   returns 200 post-restart. On failure, roll back with the command it
   prints and re-check.
6. Force one real failure (stop a non-critical deployment's upstream, or
   temporarily point a model at a bad URL) and confirm an ntfy alert
   actually arrives — same "prove the alert path" discipline
   kb/fleet-upgrade-plan-review.md's Tier-0 item zero calls for.

## Bridge safety (ticket requirement)

- No router/bridge IPs are targeted by anything here — this is gateway
  process health, not network probes. Gatus's existing config (wq#1184)
  already excludes router/bridge targets per its own header comment.
- No `ollama pull` anywhere in this ticket, cron or otherwise.
- The capped model set specifically excludes local/ollama rungs from the
  background poll so a health check can't force a cold VRAM load
  (kb/fleet-upgrade-plan-review.md §4.2) — that traffic, if the Ollama box
  sits across the bridge from the gateway, is exactly the kind of
  self-inflicted load the review flagged.
