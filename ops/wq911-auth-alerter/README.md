# wq#911 — LiteLLM auth-failure burst alerter

`lumen-litellm-auth-monitor` (wq#721) records every LiteLLM 401 to
`/home/lumen/logs/litellm/auth-failures.jsonl` but nothing consumed it: a
fleet-wide auth outage would be logged, not alerted. This adds the consumer.

## Components

- `litellm-auth-alert` — oneshot checker, installed to `/home/lumen/bin`.
  Threshold: ≥5 `auth_401` events in the last 10 min (env-overridable:
  `LITELLM_AUTH_ALERT_THRESHOLD` / `LITELLM_AUTH_ALERT_WINDOW_MIN`).
  Edge-triggered like `lumen-failed-units`: urgent ntfy + `aq add` on
  clear→breach, aq bump only while breached, low-pri ntfy on breach→clear.
  aq dedup key: `(litellm-auth, burst-401)`.
- `litellm-auth-alert.service` / `.timer` — every 2 min, so worst-case detection
  latency after the Nth 401 is ~2 min.
- `install.sh` — idempotent deploy (script + units + `enable --now`).

## Threshold rationale

Observed stray traffic (wq#903-class hardcoded-master-key stragglers) is 1–3
401s per half hour — below threshold by design; those are triaged via wq
tickets. A real outage (key rotation regression, VerificationToken wipe) blows
past 5-in-10min at fleet call volume.

## Test procedure (zero quota, no live alerts)

```
env LITELLM_AUTH_ALERT_STATE_DIR=/tmp/t AQ_DB=/tmp/t.db \
    LITELLM_AUTH_ALERT_TOPIC=lumen-test \
    LITELLM_AUTH_ALERT_THRESHOLD=1 LITELLM_AUTH_ALERT_WINDOW_MIN=1440 \
    ./litellm-auth-alert --print
```

Run 4× with thresholds 99/1/1/99 → state transitions false→true→true→false;
scratch aq shows one row bumped to x2 (dedup), not two rows. Verified
2026-08-23. Note: `lumen-test` topic 403s on the ACL'd ntfy server, so test
ntfy posts are dropped; the `lumen-alerts` path was delivery-verified
separately (primary=200).

## Related unit-layer fix (applied by opsaudit, recorded here)

`lumen-litellm-auth-monitor.service` Restart=on-failure → always:
`docker logs --follow` terminates with exit 0 on container restart — the
monitor script's comment claiming auto-reconnect is wrong (see wq#911 detail).
