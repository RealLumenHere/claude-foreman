# wq#2754 — langfuse-minio / clickhouse credential rotation

## Trigger
wq#2056 follow-up: a grep against `docker-compose.yml` printed `LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID/SECRET`
(minioadmin/minioadmin2026) and `CLICKHOUSE_PASSWORD` (langfuse2026) unmasked to session output.
Same value (`minioadmin2026`, hash e13443e9b736) was independently found in the clear in the
supergateway desktop-commander-mcp log by wq#2760/wq#2777 — this rotation closes both.

## What changed
- **MinIO root password**: `langfuse-minio` container recreated (`docker rm -f` + `docker run`,
  identical name/network(langfuse_default)/named-volume(langfuse_minio_data)/restart-policy,
  new `MINIO_ROOT_PASSWORD`). MinIO's root account is env-var-derived at every boot, not a
  persisted user-table row, so recreation is the correct rotation mechanism. Bucket data on the
  named volume was untouched.
- **ClickHouse `langfuse` user password**: this user is XML-config-driven
  (`/etc/clickhouse-server/users.d/default-user.xml`, `access_management=0`, no self-ALTER
  grant — confirmed live: a `ALTER USER langfuse IDENTIFIED WITH sha256_password BY '...'` as
  itself was correctly refused, `ACCESS_DENIED`, which is good default hardening, not a bug).
  `/entrypoint.sh` in the official image regenerates that XML file from `CLICKHOUSE_PASSWORD`
  unconditionally on every container start (verified in the entrypoint source, ~line 97-112), so
  recreating the container via `docker compose up -d` with the new env var IS the rotation.
- `docker-compose.yml` updated: `CLICKHOUSE_PASSWORD` (3 env references + 1 healthcheck),
  `LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY` + `LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY`
  (worker + web, 4 occurrences total). Access-key IDs (`minioadmin`) unchanged, only secrets.
- Recreated `langfuse-clickhouse`, `langfuse-worker`, `langfuse-web` via `docker compose up -d`
  to pick up the new env + healthcheck definitions.

## Backup
`/home/lumen/langfuse/docker-compose.yml.bak.20260926T054716Z_wq2754_rotate` (pre-rotation,
byte-identical to the untouched file — confirmed by diff before editing).

## Verification (live, post-rotation)
- `langfuse-clickhouse`, `langfuse-worker`, `langfuse-web` all `Up`/`healthy`.
- Worker log: `ClickhouseWriter` started, model-price + dashboard upserts succeeded — proves
  the new ClickHouse credential is actually being used for writes, not just present in env.
- Direct positive/negative auth checks (script-internal, no plaintext ever echoed to any tool
  call or log): `clickhouse-client` with the new password → PASS; with a wrong password → REJECTED.
  `mc ls` against `langfuse-minio` with the new secret → PASS (lists `langfuse-events`,
  `langfuse-media`); with a wrong secret → REJECTED.

## Follow-up (separate, not fixed here)
The `grep`/`cat` step that burned these creds in the first place, and the later discovery in
the supergateway MCP log, share a root cause: **nothing masks secrets at the point they're
echoed to a terminal or logged by the supergateway/desktop-commander-mcp pipe.** `mask-secrets`
exists but has to be piped in by hand every time, and it only catches URL-auth-shaped values —
it does not catch `<password>value</password>` XML or arbitrary `KEY: value` config lines (hit
this live during this rotation: `cat`ing ClickHouse's own `users.d/default-user.xml` printed
`langfuse2026` in the clear because it isn't a DSN-shaped match). Ticketed separately as a
class-level fix, not patched ad hoc here.
