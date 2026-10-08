# wq#3678 — rotate the LibreChat compose secrets

`/home/lumen/docker/docker-compose.yml` inlines three live secrets. wq#3090 and
wq#3665 each Read it whole, which burned all three (`mask_replay.py 3665`):

| compose key | sha256[:12] | also in |
|---|---|---|
| `MONGO_INITDB_ROOT_PASSWORD` | `6bc6f59051e9` | every `mongodb://lumen:…@mongodb` URI |
| `JWT_SECRET` | `19dc5bec2297` | |
| `JWT_REFRESH_SECRET` | `7d555026ff55` | |

The same hashes are behind wq#3093 (blocked since 09-29), wq#3678 and wq#3679.
guard.sh has denied Read/Grep of the file since f0873516. This runbook does the
rotation and moves the values out of the compose file for good.

## Who runs it

A privileged session with docker access. A fworker BUILD worker can't run it,
because its guard denies `docker` except `ps`/`logs` (ISSUE-LOG 797, 1028).

```
python3 ops/wq3678-librechat-secret-rotation/rotate.py           # dry run: checks, hashes only
python3 ops/wq3678-librechat-secret-rotation/rotate.py --apply   # rotate
python3 harness/mask_replay.py 3665                              # expect exit 0
```

## What `--apply` does

1. Refuses unless the live values hash to the three burned fingerprints and every
   URI password equals the root password.
2. Generates three new 64-hex values and writes them to the compose dir's `.env`
   as `LIBRECHAT_MONGO_PASSWORD`, `LIBRECHAT_JWT_SECRET` and
   `LIBRECHAT_JWT_REFRESH_SECRET`. The write is atomic with mode 0600, and
   unrelated lines stay as they are. Compose auto-loads this file, and guard.sh
   already denies `*.env`.
3. Rewrites the compose file so every literal becomes `${VAR}`. A 0600 backup
   goes to `docker-compose.yml.pre-wq3678`, and the file keeps its mode.
4. Runs `docker compose config -q`, then `mongosh` `changeUserPassword` in the
   `mongodb` container. The script reaches mongosh on stdin, never argv. If
   either step fails, the compose file is restored and Mongo stays untouched.
5. Runs `docker compose up -d` (never `--remove-orphans`), which recreates the
   services whose environment changed.
6. Checks that the new password authenticates and the old one is refused. It
   then records the three hashes with `mask_replay.record_rotated(…, ticket 3678)`
   and deletes the backup.

New JWT secrets invalidate every LibreChat session, so users must log in again.
A second run reports `already_migrated` and does nothing. Output is JSON with
key names, fingerprints and paths only.

Once this has run, the guard.sh deny on the compose file (`harness/guard.sh`,
wq#3678 block) can be lifted, because the file no longer holds any secret.
