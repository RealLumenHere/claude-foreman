#!/usr/bin/env bash
# wq#2064 — rotate the langfuse-minio root password atomically.
#
# The MinIO server reads its root password from the file named by
# MINIO_ROOT_PASSWORD_FILE (a bind-mounted docker secret), NOT from compose env.
# The langfuse web+worker clients carry the same value in 4
# LANGFUSE_S3_*_SECRET_ACCESS_KEY lines of langfuse/docker-compose.yml.
# Changing only one side causes SignatureDoesNotMatch (the 2026-09-19 outage), so
# this script changes both, bounces the whole stack, verifies, and rolls both
# back if verification fails.
#
# Usage (on Lumen, via detached systemd-run — never foreground, instinct 5):
#   rotate.sh --dry-run    resolve paths, check preconditions, change nothing
#   rotate.sh              rotate
# The new password is never printed.
set -euo pipefail

LANGFUSE_DIR=${LANGFUSE_DIR:-/home/lumen/langfuse}
LANGFUSE_COMPOSE=$LANGFUSE_DIR/docker-compose.yml
MINIO_CTR=${MINIO_CTR:-langfuse-minio}
KEYS_ENV=${KEYS_ENV:-/home/lumen/.secrets/langfuse-foreman.env}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:3020/api/public/health}
INGEST_URL=${INGEST_URL:-http://127.0.0.1:3020/api/public/ingestion}
EXPECTED_REFS=4
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

die() { echo "rotate: $*" >&2; exit 1; }
log() { echo "rotate: $*"; }

# ---- 1. map: where does the server read its password from? ----
envfile_ctr=$(docker inspect "$MINIO_CTR" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^MINIO_ROOT_PASSWORD_FILE=//p')
[ -n "$envfile_ctr" ] || die "$MINIO_CTR has no MINIO_ROOT_PASSWORD_FILE — layout changed, stop"
SECRET_FILE=$(docker inspect "$MINIO_CTR" \
  --format '{{range .Mounts}}{{.Destination}}{{"\t"}}{{.Source}}{{"\n"}}{{end}}' \
  | awk -F'\t' -v d="$envfile_ctr" '$1==d {print $2}')
[ -f "$SECRET_FILE" ] || die "secret file for $envfile_ctr did not resolve to a host file"
MINIO_PROJECT=$(docker inspect "$MINIO_CTR" --format '{{index .Config.Labels "com.docker.compose.project"}}')
MINIO_SERVICE=$(docker inspect "$MINIO_CTR" --format '{{index .Config.Labels "com.docker.compose.service"}}')
MINIO_COMPOSE=$(docker inspect "$MINIO_CTR" --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')
[ -f "$MINIO_COMPOSE" ] || die "minio compose file did not resolve (label: '$MINIO_COMPOSE')"
[ -f "$LANGFUSE_COMPOSE" ] || die "missing $LANGFUSE_COMPOSE"

old=$(tr -d '\n' < "$SECRET_FILE")
[[ $old =~ ^[A-Za-z0-9]+$ ]] || die "old password has unexpected characters; refusing to sed it"
refs=$(grep -c -E "SECRET_ACCESS_KEY: $old\$" "$LANGFUSE_COMPOSE" || true)
[ "$refs" -eq "$EXPECTED_REFS" ] || die "expected $EXPECTED_REFS client refs equal to the server secret, found $refs — sides already diverge, stop"
log "map ok: secret=$SECRET_FILE minio=$MINIO_PROJECT/$MINIO_SERVICE ($MINIO_COMPOSE) client_refs=$refs"
[ "$DRY" -eq 1 ] && { log "dry-run: nothing changed"; exit 0; }

# ---- 2. backups ----
ts=$(date +%Y%m%d_%H%M%S)
cp -p "$LANGFUSE_COMPOSE" "$LANGFUSE_COMPOSE.bak.wq2064.$ts"
cp -p "$SECRET_FILE" "$SECRET_FILE.bak.wq2064.$ts"
chmod 600 "$SECRET_FILE.bak.wq2064.$ts"

restart_all() {
  # whole stack together: drop langfuse clients, recreate minio, bring clients up.
  # never pass -v: the named volumes hold ClickHouse/Postgres/MinIO data.
  docker compose -f "$LANGFUSE_COMPOSE" down
  docker compose -p "$MINIO_PROJECT" -f "$MINIO_COMPOSE" up -d --force-recreate "$MINIO_SERVICE"
  docker compose -f "$LANGFUSE_COMPOSE" up -d
}

wait_healthy() {
  for _ in $(seq 1 60); do
    curl -fsS -o /dev/null "$HEALTH_URL" && return 0
    sleep 3
  done
  return 1
}

rollback() {
  log "ROLLBACK: restoring compose + secret file from .bak.wq2064.$ts"
  cp -p "$LANGFUSE_COMPOSE.bak.wq2064.$ts" "$LANGFUSE_COMPOSE"
  cp -p "$SECRET_FILE.bak.wq2064.$ts" "$SECRET_FILE"
  restart_all
  wait_healthy || log "rollback: web not healthy after restore — needs a human"
  exit 1
}

# ---- 3. one password, both sides, same step ----
new=$(openssl rand -hex 24)
tmp=$(mktemp "$SECRET_FILE.XXXXXX")
if [ -z "$(tail -c1 "$SECRET_FILE")" ]; then printf '%s\n' "$new" > "$tmp"; else printf '%s' "$new" > "$tmp"; fi
chmod --reference="$SECRET_FILE" "$tmp"
chown --reference="$SECRET_FILE" "$tmp" 2>/dev/null || true
sed -i -E "s|(SECRET_ACCESS_KEY: )$old\$|\1$new|" "$LANGFUSE_COMPOSE"
mv -f "$tmp" "$SECRET_FILE"
[ "$(grep -c -E "SECRET_ACCESS_KEY: $new\$" "$LANGFUSE_COMPOSE")" -eq "$EXPECTED_REFS" ] || rollback
[ "$(tr -d '\n' < "$SECRET_FILE")" = "$new" ] || rollback

# ---- 4. bounce the whole stack ----
start=$(date -u +%Y-%m-%dT%H:%M:%SZ)
restart_all || rollback
wait_healthy || rollback

# ---- 5. verify: traces write through S3, zero signature errors ----
if [ -f "$KEYS_ENV" ]; then
  # shellcheck disable=SC1090
  . "$KEYS_ENV"
  tid="wq2064-rotate-$ts"
  now=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
  payload=$(printf '{"batch":[{"id":"%s-ev","type":"trace-create","timestamp":"%s","body":{"id":"%s","name":"wq2064-rotation-probe"}}]}' "$tid" "$now" "$tid")
  code=$(curl -sS -o /dev/null -w '%{http_code}' -K <(printf 'user = "%s:%s"\n' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY") \
    -H 'Content-Type: application/json' -d "$payload" "$INGEST_URL") || rollback
  [ "$code" = 207 ] || [ "$code" = 200 ] || { log "ingestion probe returned $code"; rollback; }
  sleep 30
else
  log "WARN: $KEYS_ENV missing — skipping the trace-write probe, log check only"
  sleep 30
fi
sigerr=$(docker logs --since "$start" langfuse-worker 2>&1 | grep -c SignatureDoesNotMatch || true)
[ "$sigerr" -eq 0 ] || { log "$sigerr SignatureDoesNotMatch in langfuse-worker"; rollback; }
log "OK: rotated; 0 SignatureDoesNotMatch since $start; backups: *.bak.wq2064.$ts"
