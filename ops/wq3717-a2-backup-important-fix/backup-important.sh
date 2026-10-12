#!/usr/bin/env bash
# backup-important.sh — daily backup of important-tier data.
# Backs up: Langfuse ClickHouse, Langfuse MinIO blob store, Plausible Postgres+
# ClickHouse, LibreChat MongoDB. These are bigger / slower than the critical set
# but loss-tolerant (can be regenerated or aren't business-critical).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"

DATE_TAG=$(date +%Y%m%d)
TS=$(date +%Y%m%d_%H%M%S)
LOG="$LOG_DIR/important-$DATE_TAG.log"
mkdir -p "$LOG_DIR" "$STAGE_DIR"

# wq#3717: stage path is stable (not important-$TS) so restic's default
# parent-snapshot lookup (same --paths string as a prior snapshot) finds one
# instead of re-reading/re-hashing every file every day. The dir itself never
# persists between runs anyway (cleanup trap below removes it on EXIT) — only
# the path *string* restic sees needs to be stable.
STAGE_IMP="$STAGE_DIR/important"

# Pre-run sweep: clear a leftover stage dir from a prior killed run.
# (MinIO mirror writes root-owned files; sudo handles them. Runs before
#  STAGE_IMP is (re)created, so nothing from this run is at risk.)
sudo rm -rf "$STAGE_IMP" 2>/dev/null || true

mkdir -p "$STAGE_IMP"

cleanup() { sudo rm -rf "$STAGE_IMP" 2>/dev/null || rm -rf "$STAGE_IMP" 2>/dev/null || true; }
trap cleanup EXIT

log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

ERRORS=0

# wq#3717: containers stopped by wq#3672's Phase-1 subtraction. A leg whose
# container is not running is logged SKIP (not counted in ERRORS) only when
# it's on this list — see docs/fleet/retired_services.yaml entry
# "important-tier-phase1-subtraction". Any other stopped container is still
# a real ERROR (container crashed/misconfigured, not intentionally retired).
RETIRED_CONTAINERS=(plausible_db plausible_events_db mongodb)

container_running() {
    [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" == "true" ]]
}

container_is_retired() {
    local c
    for c in "${RETIRED_CONTAINERS[@]}"; do
        [[ "$c" == "$1" ]] && return 0
    done
    return 1
}

skip_or_error() {
    # Usage: skip_or_error <container> <leg-description>
    local container="$1" desc="$2"
    if container_is_retired "$container"; then
        log "  SKIP $desc ($container not running, retired per wq#3672)"
    else
        log "  FAILED $desc ($container not running)"; ERRORS=$((ERRORS+1))
    fi
}

# ──────────────────────────────────────────────────────────────────────
# 1. Langfuse ClickHouse — dump traces / observations / scores tables
# ──────────────────────────────────────────────────────────────────────
log "=== Langfuse ClickHouse dump ==="
CH_DUMP="$STAGE_IMP/langfuse-clickhouse-Native.tar.gz"
# clickhouse-client BACKUP requires storage_configuration that Langfuse's image
# doesn't have. Fall back to per-table SELECT INTO OUTFILE for reliability.
TABLES=(traces observations scores)
LF_CH_DIR="$STAGE_IMP/langfuse-ch"
mkdir -p "$LF_CH_DIR"
CH_OK=1
for tbl in "${TABLES[@]}"; do
    out="$LF_CH_DIR/${tbl}.tsv.gz"
    if docker exec langfuse-clickhouse sh -c 'exec clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" --database default --query "$1"' _ \
        "SELECT * FROM $tbl FORMAT TabSeparatedWithNamesAndTypes" 2>>"$LOG" | gzip > "$out"; then
        sz=$(stat -c%s "$out" 2>/dev/null || echo 0)
        log "  $tbl: $sz bytes"
    else
        log "  FAILED dump $tbl"; CH_OK=0
    fi
done
if [[ $CH_OK -eq 0 ]]; then ERRORS=$((ERRORS+1)); fi

# ──────────────────────────────────────────────────────────────────────
# 2. Langfuse MinIO — mirror via mc into stage dir, then snapshot dir
# ──────────────────────────────────────────────────────────────────────
log "=== Langfuse MinIO mirror ==="
MINIO_DIR="$STAGE_IMP/minio"
mkdir -p "$MINIO_DIR"
# Use the official mc image on the langfuse_default network so it can reach langfuse-minio
MINIO_U=$(docker exec langfuse-minio sh -c 'echo "$MINIO_ROOT_USER"' 2>/dev/null); MINIO_P=$(docker exec langfuse-minio sh -c 'echo "$MINIO_ROOT_PASSWORD"' 2>/dev/null)
if docker run --rm --network langfuse_default \
        -e "MC_HOST_lf=http://${MINIO_U}:${MINIO_P}@langfuse-minio:9000" \
        -v "$MINIO_DIR:/out" \
        --entrypoint sh \
        minio/mc -c "mc mirror --quiet lf/langfuse-events /out/events && mc mirror --quiet lf/langfuse-media /out/media" 2>>"$LOG"; then
    sz=$(du -sh "$MINIO_DIR" 2>/dev/null | awk '{print $1}')
    log "  mirrored $sz"
else
    log "  FAILED MinIO mirror (continuing)"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
# 3. Plausible Postgres — pg_dump
# ──────────────────────────────────────────────────────────────────────
log "=== Plausible Postgres dump ==="
if ! container_running plausible_db; then
    skip_or_error plausible_db "Plausible Postgres dump"
else
    PL_PG_DUMP="$STAGE_IMP/plausible-pg.sql.gz"
    # Find DB name & user via env on the container
    PL_USER=$(docker exec plausible_db sh -c 'echo $POSTGRES_USER' 2>/dev/null || echo postgres)
    PL_DB=$(docker exec plausible_db sh -c 'echo $POSTGRES_DB' 2>/dev/null || echo plausible_db)
    if docker exec plausible_db pg_dumpall -U "$PL_USER" 2>>"$LOG" | gzip > "$PL_PG_DUMP"; then
        log "  dumped $(stat -c%s "$PL_PG_DUMP" 2>/dev/null) bytes (user=$PL_USER db=$PL_DB)"
    else
        log "  FAILED Plausible Postgres dump"; ERRORS=$((ERRORS+1))
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# 4. Plausible events ClickHouse — dump events table
# ──────────────────────────────────────────────────────────────────────
log "=== Plausible events ClickHouse dump ==="
if ! container_running plausible_events_db; then
    skip_or_error plausible_events_db "Plausible events ClickHouse dump"
else
    PL_CH_DIR="$STAGE_IMP/plausible-ch"
    mkdir -p "$PL_CH_DIR"
    PL_CH_OK=1
    # List tables first
    TABLES_RAW=$(docker exec plausible_events_db clickhouse-client --query "SHOW TABLES FROM plausible_events_db" 2>>"$LOG" || true)
    if [[ -n "$TABLES_RAW" ]]; then
        while IFS= read -r tbl; do
            [[ -z "$tbl" ]] && continue
            out="$PL_CH_DIR/${tbl}.tsv.gz"
            if docker exec plausible_events_db clickhouse-client \
                --database plausible_events_db \
                --query "SELECT * FROM $tbl FORMAT TabSeparatedWithNamesAndTypes" 2>>"$LOG" | gzip > "$out"; then
                sz=$(stat -c%s "$out" 2>/dev/null || echo 0)
                log "  $tbl: $sz bytes"
            else
                log "  FAILED dump plausible $tbl"; PL_CH_OK=0
            fi
        done <<< "$TABLES_RAW"
    else
        log "  no tables listed for plausible_events_db"; PL_CH_OK=0
    fi
    if [[ $PL_CH_OK -eq 0 ]]; then ERRORS=$((ERRORS+1)); fi
fi

# ──────────────────────────────────────────────────────────────────────
# 5. MongoDB — mongodump --archive
# ──────────────────────────────────────────────────────────────────────
log "=== MongoDB dump ==="
if ! container_running mongodb; then
    skip_or_error mongodb "MongoDB dump"
else
    MG_DUMP="$STAGE_IMP/mongodb.archive.gz"
    # Credentials live in $MONGO_CREDENTIALS_FILE (env.sh), not inline (wq#3717).
    # shellcheck disable=SC1090
    source "$MONGO_CREDENTIALS_FILE"
    # mongodump is available inside the mongo:7 container.
    if docker exec mongodb sh -c 'exec mongodump --username="$1" --password="$2" --authenticationDatabase=admin --archive --gzip 2>/dev/null' _ \
        "$MONGO_BACKUP_USER" "$MONGO_BACKUP_PASSWORD" > "$MG_DUMP" 2>>"$LOG"; then
        log "  dumped $(stat -c%s "$MG_DUMP" 2>/dev/null) bytes"
    else
        log "  FAILED MongoDB dump (continuing)"; ERRORS=$((ERRORS+1))
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# 5.5 Gitea (Anubis) — pull nightly `gitea dump` zips into the flow.
#     Anubis runs gitea-dump.timer at 00:45 UTC (repos + sqlite DB + config,
#     keeps last 3). Dumps are gitea-owned 600, hence sudo rsync-path.
#     Added 2026-07-10 (Claude) — Gitea had NO backup path before this.
# ──────────────────────────────────────────────────────────────────────
log "=== Gitea dump pull (Anubis) ==="
GITEA_DIR="$STAGE_IMP/gitea-dumps"
mkdir -p "$GITEA_DIR"
if rsync -a --timeout=120 --rsync-path="sudo -n rsync" \
        -e "ssh -o ConnectTimeout=15 -o BatchMode=yes" \
        anubis@100.121.17.5:/var/lib/gitea/dumps/ "$GITEA_DIR/" 2>>"$LOG"; then
    log "  pulled $(du -sh "$GITEA_DIR" 2>/dev/null | awk '{print $1}')"
else
    log "  FAILED Gitea dump pull (continuing)"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
# 5.6 VictoriaMetrics (Bes) — snapshot + pull power-telemetry TSDB (wq#347).
#     Skips cleanly if VM isn't enabled yet (wq#341 staged). When wq#330's 2TB
#     lands, switch to vmbackup writing there + keep restic for the snapshot dir.
# ──────────────────────────────────────────────────────────────────────
log "=== VictoriaMetrics pull (Bes) ==="
if /home/lumen/stage/vmbackup/vmbackup-pull.sh "$STAGE_IMP/vm-bes" >>"$LOG" 2>&1; then
    log "  vm pull ok ($(du -sh "$STAGE_IMP/vm-bes" 2>/dev/null | awk '{print $1}'))"
else
    log "  FAILED VM pull (continuing)"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
# 6. Snapshot to both repositories
# ──────────────────────────────────────────────────────────────────────
for REPO in "$REPO_LOCAL" "$REPO_MIRROR"; do
    log "=== restic backup -> $REPO ==="
    # wq#566 (2026-08-14): LPP events/ rides this DAILY tier as a DIRECT path, not staged —
    # no 27M daily copy, snapshots restore to the real /home/lumen/pokemon/events path, and
    # the uncompressed .jsonl files dedup well across days (unlike the gzip'd checkpoints,
    # which is exactly why THOSE live in the hourly critical tier instead — see wq#556).
    if "$RESTIC_BINARY" -r "$REPO" --retry-lock 50m backup \
        --tag important --tag "host=lumen" --tag "ts=$TS" \
        "$STAGE_IMP" "/home/lumen/pokemon/events" >>"$LOG" 2>&1; then
        log "  OK"
    else
        log "  FAILED restic backup to $REPO"; ERRORS=$((ERRORS+1))
    fi
done

log "=== DONE — errors=$ERRORS ==="
exit $ERRORS
