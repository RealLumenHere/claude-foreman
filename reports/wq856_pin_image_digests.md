# Container Image Digest Pinning — wq#856

**Deployed: 2026-08-23 by Claude (session wq#856)**

## What was done

All fleet container images are now pinned to their specific registry
manifest digests (`image: repo:tag@sha256:...`). A `docker compose pull`
or `docker compose up -d` will NOT silently upgrade any image — the
digest locks the exact binary.

## Pinned compose files (28 image references)

| File | Images pinned |
|---|---|
| `/home/lumen/docker/docker-compose.yml` | mongo:7, ntfy, librechat, ubuntu:24.04 (litellm was already pinned) |
| `/home/lumen/langfuse/docker-compose.yml` | redis:7-alpine, clickhouse:24.3.3.102-alpine, postgres:16-alpine, langfuse-worker:3, langfuse:3 |
| `/home/lumen/plausible/docker-compose.yml` | postgres:16-alpine, clickhouse:24.3.3.102-alpine, plausible:v2.1.4 |
| `/home/lumen/actual-budget/docker-compose.yml` | actual-server:latest, nginx:alpine |
| `/home/lumen/glance/docker-compose.yml` | glance:latest (was already pinned by prior worker) |
| `/home/lumen/sentinel/docker-compose.yml` | gatus:v5.10.0 |
| `/home/lumen/dify/docker/docker-compose.yaml` | dify-api:1.14.2 (x4), dify-web:1.14.2, postgres:15-alpine, redis:6-alpine, dify-sandbox:0.2.15, dify-plugin-daemon:0.6.1-local, squid:latest, nginx:latest, weaviate:1.27.0, pgvector:pg16, busybox:latest |

## Standalone containers (recreated with pinned digest)

| Container | Image@digest |
|---|---|
| `lumen-pgvector` | `pgvector/pgvector:pg16@sha256:a36250871de0...` |
| `langfuse-minio` | `quay.io/minio/minio:latest@sha256:14cea493d9a3...` |

## Excluded (local builds, no registry)

| Container | Reason |
|---|---|
| `postgres-mcp-plausible` | `postgres-mcp:local` — locally built, no registry digest |
| `postgres-mcp-langfuse` | `postgres-mcp:local` — locally built, no registry digest |

## Backups

All original compose files backed up as `*.bak.20260823_124229_pre_digest_pin`
in their respective directories. Pinning script at `/home/lumen/bin/pin_digests.py`.

---

## Update path (how to upgrade a container)

### For compose-managed containers

```bash
# 1. Check what's available upstream
docker pull <repo>:<tag>   # pulls latest for that tag

# 2. Get the new digest
docker images --digests <repo>:<tag> --format '{{.Digest}}'

# 3. Edit the compose file — replace the old @sha256:... with the new one
#    Example: image: langfuse/langfuse:3@sha256:<NEW_DIGEST>

# 4. Recreate just that service
cd /path/to/compose/dir
docker compose up -d --force-recreate <service-name>

# 5. Verify health
docker ps --filter name=<container-name>
```

### For standalone containers (pgvector, minio)

```bash
# 1. Pull new image
docker pull <repo>:<tag>

# 2. Get digest
docker images --digests <repo>:<tag> --format '{{.Digest}}'

# 3. Stop + remove old container
docker stop <name> && docker rm <name>

# 4. Run with new pinned digest (copy the docker run command from this doc
#    or from the recreate scripts, updating the digest)
docker run -d --name <name> ... <repo>:<tag>@sha256:<NEW_DIGEST> ...

# 5. Verify
docker ps --filter name=<name>
```

### Recreate commands for standalone containers

**pgvector:**
```bash
docker stop lumen-pgvector && docker rm lumen-pgvector
docker run -d \
  --name lumen-pgvector \
  --restart unless-stopped \
  -p 127.0.0.1:5433:5432 \
  -v /home/lumen/data/pgvector:/var/lib/postgresql/data \
  -e POSTGRES_DB=research \
  -e POSTGRES_USER=lumen \
  -e POSTGRES_PASSWORD=9W0M16qmmS8t6m0pwVEChw8k \
  --network docker_default \
  pgvector/pgvector:pg16@sha256:<DIGEST>
```

**minio:**
```bash
docker stop langfuse-minio && docker rm langfuse-minio
docker run -d \
  --name langfuse-minio \
  --restart unless-stopped \
  -v langfuse_minio_data:/data \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin2026 \
  --network langfuse_default \
  quay.io/minio/minio:latest@sha256:<DIGEST> \
  server /data --console-address :9001
```

### Batch upgrade helper

To upgrade ALL images at once (e.g., monthly maintenance window):

```bash
# Pull all current tags, resolve new digests, update compose files
python3 /home/lumen/bin/pin_digests.py   # update DIGEST_MAP first!
# Then recreate each stack:
for dir in docker langfuse plausible actual-budget glance sentinel; do
  cd /home/lumen/$dir && docker compose up -d --force-recreate
done
cd /home/lumen/dify/docker && docker compose up -d --force-recreate
# Standalone:
# (recreate pgvector + minio with new digests per commands above)
```
