# wq#667 — Gatus send-on-resolved gap on external/heartbeat endpoints

## Confirmed
- sentinel-gatus pinned `twinproduction/gatus:v5.10.0` (docker-compose.yml, wq#856).
- `config.yaml` already has `default-alert.send-on-resolved: true` and every
  5-probes endpoint alert also sets it explicitly — not a config omission.
- aq history shows every 5-probes Gatus row (source=`ntfy:lumen-alerts`,
  key=`Gatus: 5-probes/<name>`) that ever closed was closed by a human/manual
  note, never by the `ntfy_alert_subscriber.py` recovery path (wq#548), which
  only fires on Gatus's own "An alert has been resolved" ntfy message — a
  message these endpoints never send, confirmed across multiple verified
  healthy windows (aq#544, aq#557, and live row aq#805 today).
- Could not reach the Gatus changelog/GitHub issues to confirm this is a
  known upstream limitation vs a local misconfig — this build lane has no
  network access (WebSearch/WebFetch aren't allow-listed for headless
  workers). Treat the Gatus-side root cause as circumstantial, not
  upstream-verified.

## Fix shipped (option b from the ticket)
Extended `sentinel_probes.py`'s existing per-probe edge-clear (wq#548, which
already auto-resolves `source='probe'` rows on PASS) to also auto-resolve the
matching `source='ntfy:lumen-alerts'`, `key='Gatus: 5-probes/<name>'` row.
sentinel_probes.py is the one thing that reliably knows a probe recovered
regardless of whether Gatus ever emits a resolve notification.

Verified live: ran the real probe cycle
(`python3 /home/lumen/sentinel/probes/sentinel_probes.py`), `probe-storage-integrity`
PASSed and the previously-open aq#805 (`Gatus: 5-probes/probe-storage-integrity`,
open since 2026-08-23T05:59:18Z) flipped to `resolved` in the same cycle.

## Not done
Did not touch Gatus config/version — no evidence a config change would help,
and no network access to check for a newer Gatus release that fixes external-
endpoint alert evaluation. If wq867/a future ticket gets network access,
worth checking the Gatus issue tracker for "external endpoint" + "resolved"
to see if this is a known bug fixed in a later tag.
