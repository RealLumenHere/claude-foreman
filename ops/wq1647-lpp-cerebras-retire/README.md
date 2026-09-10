# wq#1647 — lpp-cerebras key at 100% of $10: decision = RETIRE

## Alert

litellm-budget-alert 2026-09-10 06:15Z: `lpp-cerebras` $10.0038 / $10.0 (100.0%).
Ticket asked: raise max_budget (lpp2 precedent, 5→40) or rotate?

## Answer: neither — retire the key (max_budget = 0)

The key is orphaned. Evidence:

- `systemctl show lumen-pokemon-live -p EnvironmentFiles` →
  `/home/lumen/.secrets/litellm-service-keys/lpp-glm.env` (checked live
  2026-09-10). All four brain legs plus the cerebras fallback rung ride the
  `lpp-glm` key, whose allowlist is `{glm-zai, glm, cerebras, qwen36-local,
  ds-flash}` at a $20/30d cap with ~$0.006 spent
  (research/FLEET_COST_PLAN.md §8.2–8.3).
- The $10 burn was the wq#1578 misconfiguration: the live service ran glm brain
  roles on the cerebras-scoped key, so 4,619 calls failed
  `can_key_call_model` and the cerebras fallback rung rescued everything until
  the cap (pokemon/foreman/research/LPP_BEHAVIORAL_ANALYSIS.md E7). That env
  file has since been corrected to `lpp-glm.env`.
- Repo-wide grep for `lpp-cerebras` finds only historical docs — no live
  consumer.

Why not the alternatives:

- **Raise**: any future spend on this key is a bug (its consumer moved).
  Refusal-at-cap is exactly the failsafe we want, and a plain $10 cap would
  silently reopen when the 30d window rolls.
- **Rotate**: rotation is for compromised keys. A rotated key gets a fresh
  spend window, which would silently re-enable any stale consumer.

`max_budget=0` makes the refusal permanent (LiteLLM refuses when
`spend >= max_budget`, so even a reset `spend=0` stays refused — verified by
live probe, http 400 budget refusal) and stops the 6-hourly alert noise:
`litellm-budget-alert` skips keys with `budget <= 0`
(/home/lumen/bin/litellm-budget-alert, `if budget <= 0: continue`) while the
gateway record still exists, so the "rotated or revoked?" failure path does not
fire either.

## Applied

`python3 ops/wq1647-lpp-cerebras-retire/retire_lpp_cerebras.py` — idempotent;
run output recorded in the PR. Matches the local key file to its gateway record
by sha256 token via `/key/list` (wq#948 — secret never in a URL; nothing secret
printed).

## Left for ops

The local `lpp-cerebras.key`/`.env` files still exist under
`~/.secrets/litellm-service-keys/`. Deleting the gateway key + files is
optional cleanup; doing it before removing the files would trip
budget-alert's "matches no key on the gateway" alarm, so delete both together
or not at all.

Note: `lpp-2` is also at 100% of $40 — separate key, separate decision, not
this ticket. The budget-alert exit-1-by-design noise is wq#1657.
