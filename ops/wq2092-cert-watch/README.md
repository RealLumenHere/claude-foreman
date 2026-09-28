# wq#2092 — lumen-cert-watch

Daily TLS leaf-cert expiry watcher for fleet HTTPS endpoints. Gatus checks
health; nothing checked the certificate itself.

- **Targets:** every non-wildcard `hostname:` in the cloudflared ingress
  (`/etc/cloudflared/config.yml`, same discovery as `bin/ui-inventory`), port
  443, plus `targets.json` `extra` entries (`{"host": ..., "port": ...}`) and
  `--extra HOST[:PORT]`. `extra` ships empty: no internal nginx TLS listener
  has a verified host:port yet, so none are invented. Add them there. A
  malformed `targets.json` (bad JSON, or an entry with no `host` / a
  non-numeric `port`) is skipped with one deduped ntfy — the cloudflared hosts
  are still checked, and `install.sh --verify` fails on it (wq#3008).
- **Alerts:** when a host first crosses 21d (default), 7d (high), 1d
  (urgent) or is expired (urgent) — an `aq` row (`source=cert-watch`,
  `key=host:port`) plus `ntfy-send`. Only a move to a *more severe* tier
  alerts; a renewal sends one "renewed" notice and resolves the aq row.
  Endpoints that cannot be reached get one aggregated notice per outage.
- **Read-only:** opens a TLS handshake (verification off, so an expired or
  self-signed leaf can still be read) and writes one state file,
  `/home/lumen/logs/.lumen-cert-watch.state.json`.
- **Out of scope:** the source paper's agentic encrypted-traffic anomaly
  detection (needs traffic inspection and a model).

## Install / rollback

```
lumen-run start --root -- bash /home/lumen/foreman/ops/wq2092-cert-watch/install.sh
python3 /home/lumen/services/lumen-cert-watch/cert_watch.py run --dry-run
```

Deployed 2026-09-28 (28 cloudflared-fronted hostnames discovered from the live
ingress, all `ok`). `install.sh --verify` answers the wq#1696 deploy-verify
contract read-only, so `ops-deploy-drift` alerts authoritatively if the timer
ever stops or the installed copy drifts:

```
bash /home/lumen/foreman/ops/wq2092-cert-watch/install.sh --verify
```

Rollback: `systemctl disable --now lumen-cert-watch.timer`, remove
`/etc/systemd/system/lumen-cert-watch.{service,timer}` and
`/home/lumen/services/lumen-cert-watch/`. Nothing else is mutated.

## Tests

`python3 -m unittest discover -s ops/wq2092-cert-watch` — includes a real
handshake against a loopback TLS server with a throwaway openssl cert.
