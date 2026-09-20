# wq#2064 — rotate the leaked langfuse-minio root password

Status: script written, **not yet run and not yet syntax-checked** (the BUILD worker that wrote it
had no docker access and `bash -n` is not on its allowlist). Rotation is still OPEN; the stack is
still on the leaked password (docker-internal only, port 9000 not host-published).

## Map (why the 2026-09-19 attempt failed)

| Side | Where the password lives |
|---|---|
| MinIO server | `MINIO_ROOT_PASSWORD_FILE` -> bind-mounted docker secret `secret_key` (separate "shared" compose, `langfuse/DEPLOYMENT.md:16`; host path is discoverable only via `docker inspect langfuse-minio`) |
| Clients | 4 lines `LANGFUSE_S3_{EVENT,MEDIA}_UPLOAD_SECRET_ACCESS_KEY` in `/home/lumen/langfuse/docker-compose.yml` (2 in `langfuse-worker`, 2 in `langfuse-web`) |

Changing only the client side gives `SignatureDoesNotMatch`. The langfuse compose file has no minio
service, so `down`/`up` of that file alone never touches the server.

## Run

Detached, from a session that can reach docker (Commander path), never foreground:

    /path/to/rotate.sh --dry-run     # resolve + assert 4 refs match the server secret; changes nothing
    systemd-run --user --unit=wq2064-rotate /path/to/rotate.sh

`rotate.sh` backs up both files (`*.bak.wq2064.<ts>`), writes one `openssl rand -hex 24` value to both
in one step, bounces langfuse-down / minio-recreate / langfuse-up, posts a probe trace, and requires
0 `SignatureDoesNotMatch` in `langfuse-worker` logs since the restart. On any failure it restores both
backups and restarts again.

## After it lands

- Rotate the other inline credentials in that compose file (redis, SALT, ENCRYPTION_KEY, NEXTAUTH_SECRET,
  init user password): they were printed to a worker transcript while mapping this ticket.
- Ticket item 5 (single source of truth): langfuse env has no `_FILE` support, so the clients cannot read
  the secret file directly. Follow-up: move all inline secrets to a 0600 `.env` beside the compose and
  reference them as `${VAR:?}`.
