#!/usr/bin/env bash
# backup-critical.sh — hourly backup of critical-tier data.
# Backs up: Vaultwarden (PG dump from maat + data dir, wq#3694), work/alert queue SQLite + .secrets + agent/.env + litellm config + cloudflared (wq#3693), Langfuse Postgres, lumen-pgvector, Redis DB 2,
# /home/lumen/agent SQLite files (.backup'd before snapshot), claude_memory.md,
# /home/lumen/decisions, /home/lumen/scratch (small subset).
#
# Snapshots each repo with restic and tags the snapshot 'critical'.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/env.sh"

DATE_TAG=$(date +%Y%m%d)
TS=$(date +%Y%m%d_%H%M%S)
LOG="$LOG_DIR/critical-$DATE_TAG.log"
mkdir -p "$LOG_DIR"
# STAGE_DIR lives under /scratch (RAID0 ephemeral, intentional since 2026-06-19).
# /scratch is recreated root-owned on reboot, so a plain mkdir -p by the lumen
# service user cannot create/write the stage subdir -> every dump fails "cannot
# open" (wq#268 root cause, broke 2026-07-15). Ensure it exists AND is writable,
# using sudo only if the plain path fails. Fail loud if we still can't write.
if ! mkdir -p "$STAGE_DIR" 2>/dev/null || [ ! -w "$STAGE_DIR" ]; then
    sudo -n mkdir -p "$STAGE_DIR" 2>/dev/null || true
    sudo -n chown "$(id -un):$(id -gn)" "$STAGE_DIR" 2>/dev/null || true
fi
if [ ! -w "$STAGE_DIR" ]; then
    echo "[$(date -u +%FT%TZ)] FATAL: STAGE_DIR $STAGE_DIR not writable" | tee -a "$LOG"
    exit 1
fi

# Helper: log to file + stdout
log() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

cleanup() {
    rm -f "$STAGE_DIR"/*.sql.gz "$STAGE_DIR"/*.rdb "$STAGE_DIR"/*.sqlite3 "$STAGE_DIR"/*.db 2>/dev/null || true
    # wq#3693/3694: staged vault dump+tar, cloudflared copy (holds tunnel creds) must not linger on /scratch
    rm -f "$STAGE_DIR"/vaultwarden-pg.dump "$STAGE_DIR"/vaultwarden-data.tar.gz 2>/dev/null || true
    rm -rf "$STAGE_DIR"/etc-cloudflared 2>/dev/null || true
}
trap cleanup EXIT

ERRORS=0

# ──────────────────────────────────────────────────────────────────────
# 1. Vaultwarden — online SQLite .backup, then snapshot the dump
# ──────────────────────────────────────────────────────────────────────
# 1b. Minerva state store — bi-temporal fleet facts + episodic memory (CRITICAL)
#     Online SQLite .backup (atomic regardless of journal mode). Was previously
#     UNBACKED (find scoped to /home/lumen/agent, missed /home/lumen/minerva).
# ──────────────────────────────────────────────────────────────────────
log "=== Minerva state store dump ==="
MIN_DUMP="$STAGE_DIR/minerva.db.sqlite3"
if sqlite3 -cmd ".timeout 30000" /home/lumen/minerva/minerva.db ".backup '$MIN_DUMP'" 2>>"$LOG"; then
    log "  dumped $(stat -c%s "$MIN_DUMP" 2>/dev/null) bytes"
else
    log "  FAILED Minerva state store dump"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
log "=== Vaultwarden (live PG on maat, standby sobek as fallback) ==="
# wq#3694: the old step dumped /home/lumen/vaultwarden/data/db.sqlite3 — a 2026-06-03 fossil from before
# the HA migration. Production Vaultwarden is PostgreSQL 18 on maat (+ streaming standby sobek).
# Replication is not a backup (a bad DELETE replicates in ms), so take a point-in-time pg_dump -Fc
# PLUS the data dir (rsa_key.pem!) every hour. Restore order: fleet-config/docs/recovery.md 5.2.
VW_PG_DUMP="$STAGE_DIR/vaultwarden-pg.dump"
VW_DATA_TAR="$STAGE_DIR/vaultwarden-data.tar.gz"
VW_SSHO=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15)
VW_OK=0
for VW_TARGET in "maam@100.105.219.34:maat" "sobek@100.86.156.91:sobek"; do
    VW_SSH="${VW_TARGET%%:*}"; VW_NODE="${VW_TARGET##*:}"
    rm -f "$VW_PG_DUMP" "$VW_DATA_TAR"
    if ssh "${VW_SSHO[@]}" "$VW_SSH" "sudo -n -u postgres pg_dump -Fc vaultwarden" > "$VW_PG_DUMP" 2>>"$LOG" \
       && [ "$(stat -c%s "$VW_PG_DUMP" 2>/dev/null || echo 0)" -gt 10000 ] \
       && [ "$(head -c5 "$VW_PG_DUMP")" = "PGDMP" ] \
       && ssh "${VW_SSHO[@]}" "$VW_SSH" "sudo -n tar czf - -C /opt/${VW_NODE}-vaultwarden --exclude=./backups ." > "$VW_DATA_TAR" 2>>"$LOG" \
       && tar tzf "$VW_DATA_TAR" 2>/dev/null | grep -q rsa_key.pem; then
        log "  vault pg_dump from $VW_NODE: $(stat -c%s "$VW_PG_DUMP") bytes; data tar: $(stat -c%s "$VW_DATA_TAR") bytes"
        VW_OK=1; break
    fi
    log "  vault dump via $VW_NODE failed validation — trying next target"
done
if [ "$VW_OK" -ne 1 ]; then
    rm -f "$VW_PG_DUMP" "$VW_DATA_TAR"
    log "  FAILED Vaultwarden PG dump (all targets)"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
# 2. Langfuse Postgres — pg_dump
# ──────────────────────────────────────────────────────────────────────
log "=== Langfuse Postgres dump ==="
# wq#3912: Langfuse retired 2026-10-14
if ! docker ps -q -f name=langfuse-postgres 2>/dev/null | grep -q .; then
    log "  SKIP Langfuse Postgres dump (container retired per wq#3912)"
else
    LF_DUMP="$STAGE_DIR/langfuse-pg.sql.gz"
    if docker exec langfuse-postgres pg_dump -U langfuse langfuse 2>>"$LOG" | gzip > "$LF_DUMP"; then
        log "  dumped $(stat -c%s "$LF_DUMP" 2>/dev/null) bytes"
    else
        log "  FAILED Langfuse Postgres dump"; ERRORS=$((ERRORS+1))
    fi
fi

# ──────────────────────────────────────────────────────────────────────
# 3. lumen-pgvector — pg_dump (RAG embeddings / research index)
# ──────────────────────────────────────────────────────────────────────
log "=== lumen-pgvector dump ==="
PV_DUMP="$STAGE_DIR/pgvector.sql.gz"
# Default user/db for pgvector — verify via docker exec env if needed.
if docker exec lumen-pgvector pg_dumpall -U lumen 2>>"$LOG" | gzip > "$PV_DUMP"; then
    log "  dumped $(stat -c%s "$PV_DUMP" 2>/dev/null) bytes"
else
    log "  FAILED pgvector dump (continuing)"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
# 4. Redis DB 2 — fleet quota state. Uses --rdb for consistent snapshot.
# ──────────────────────────────────────────────────────────────────────
log "=== Redis DB 2 dump ==="
RDB="$STAGE_DIR/redis-db2.rdb"
# redis-cli --rdb writes a single RDB of the CURRENT db; we want db 2.
if redis-cli -n 2 --rdb "$RDB" 2>>"$LOG"; then
    log "  dumped $(stat -c%s "$RDB" 2>/dev/null) bytes"
else
    log "  FAILED redis dump"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
# 5. Agent SQLite files — .backup each cleanly (no raw copy of open DBs)
# ──────────────────────────────────────────────────────────────────────
log "=== Agent SQLite databases ==="
AGENT_DUMP_DIR="$STAGE_DIR/agent-sqlite"
mkdir -p "$AGENT_DUMP_DIR"
rm -f "$AGENT_DUMP_DIR"/*.sqlite3 2>/dev/null || true

# Discover all *.db / *.sqlite / *.sqlite3 under /home/lumen/agent
while IFS= read -r src; do
    base=$(basename "$src")
    dst="$AGENT_DUMP_DIR/${base}.sqlite3"
    if sqlite3 -cmd ".timeout 30000" "$src" ".backup '$dst'" 2>>"$LOG"; then
        :
    else
        log "  FAILED sqlite backup of $src"; ERRORS=$((ERRORS+1))
    fi
done < <(find /home/lumen/agent -maxdepth 3 \( -name "*.db" -o -name "*.sqlite" -o -name "*.sqlite3" \) -type f 2>/dev/null)
log "  $(ls "$AGENT_DUMP_DIR" | wc -l) sqlite files dumped"

# ──────────────────────────────────────────────────────────────────────
# 5b. wq#3693 — /home/lumen/agents (work queue, alert queue, failure queue...) + ntfy + beszel SQLite.
#     Section 5 only walks /home/lumen/agent (singular); /home/lumen/agents was never swept, so
#     queue.db / alerts.db had no scheduled backup. Names are path-flattened to avoid basename clashes.
# ──────────────────────────────────────────────────────────────────────
log "=== agents/ + ntfy + beszel SQLite databases ==="
AGENTS_DUMP_DIR="$STAGE_DIR/agents-sqlite"
mkdir -p "$AGENTS_DUMP_DIR"
rm -f "$AGENTS_DUMP_DIR"/*.sqlite3 2>/dev/null || true
backup_sqlite_flat() {  # $1 = source db, $2 = flat name
    if sqlite3 -cmd ".timeout 30000" "$1" ".backup '$AGENTS_DUMP_DIR/$2.sqlite3'" 2>>"$LOG"; then
        :
    else
        log "  FAILED sqlite backup of $1"; ERRORS=$((ERRORS+1))
    fi
}
while IFS= read -r src; do
    [ -s "$src" ] || continue   # 0-byte placeholder files (wq.db, aq.db) are not databases
    rel="${src#/home/lumen/agents/}"
    backup_sqlite_flat "$src" "${rel//\//__}"
done < <(find /home/lumen/agents -maxdepth 3 \( -name "*.db" -o -name "*.sqlite" -o -name "*.sqlite3" \) -type f 2>/dev/null)
for extra_db in /home/lumen/ntfy-data/auth.db /home/lumen/beszel/beszel_data/data.db /home/lumen/beszel/beszel_data/auxiliary.db; do
    [ -s "$extra_db" ] && backup_sqlite_flat "$extra_db" "$(echo "${extra_db#/home/lumen/}" | tr / _)"
done
log "  $(ls "$AGENTS_DUMP_DIR" | wc -l) sqlite files dumped to agents-sqlite/"

# 5c. wq#3693 — /etc/cloudflared is root:root 0600 (tunnel credentials + cert). Copy via sudo into
#     the stage dir (extracted as lumen), restic snapshots it, cleanup trap removes it.
log "=== /etc/cloudflared (root-owned) ==="
CF_STAGE="$STAGE_DIR/etc-cloudflared"
rm -rf "$CF_STAGE"; mkdir -p "$CF_STAGE"
if sudo -n tar -C /etc -cf - cloudflared 2>>"$LOG" | tar -C "$CF_STAGE" -xf - 2>>"$LOG"; then
    log "  copied $(find "$CF_STAGE" -type f | wc -l) files"
else
    log "  FAILED /etc/cloudflared copy"; ERRORS=$((ERRORS+1))
fi

# ──────────────────────────────────────────────────────────────────────
# 6. Snapshot all the above to both repositories
# ──────────────────────────────────────────────────────────────────────
PATHS_TO_BACKUP=(
    "$STAGE_DIR"
    "/home/lumen/claude_memory.md"
    "/home/lumen/decisions"
    "/home/lumen/services/backup/scripts"
    "/home/lumen/services/backup/units"
)

# wq#3693: config/secret paths that were never in any tier (agent-sqlite for agents/, ntfy, beszel and
# /etc/cloudflared arrive via $STAGE_DIR above). .secrets/gate-snapshots (1 GB of pytest token fixtures)
# is excluded below.
PATHS_TO_BACKUP+=("/home/lumen/.secrets")
PATHS_TO_BACKUP+=("/home/lumen/agent/.env")
PATHS_TO_BACKUP+=("/home/lumen/agent/litellm_config.yaml")
PATHS_TO_BACKUP+=("/home/lumen/docker/docker-compose.yml")
PATHS_TO_BACKUP+=("/home/lumen/sentinel/config")
PATHS_TO_BACKUP+=("/home/lumen/beszel/beszel_data")

# Add scratch (small text outputs only — exclude regenerable cache dirs)
PATHS_TO_BACKUP+=("/home/lumen/scratch")

# Credential/secret files (eBay API creds, scoped LiteLLM keys, etc.)
PATHS_TO_BACKUP+=("/home/lumen/secrets")

# Disaster runbook + escrow (wq#308) — the recovery docs MUST survive the disaster.
# NOTE: escrow-*.gpg is already encrypted (gpg AES256); the runbook is non-secret.
PATHS_TO_BACKUP+=("/home/lumen/RUNBOOK.md")
PATHS_TO_BACKUP+=("/home/lumen/escrow")

# ScreenPad / RTD controller firmware archive (git repo of board dumps)
PATHS_TO_BACKUP+=("/home/lumen/screenpad-firmware")

# Mac Pro 6,1 BootROM baseline dumps (brick-recovery insurance, wq#155)
PATHS_TO_BACKUP+=("/home/lumen/backups/mp61-rom")

# Conversation-learning archive (knob2=11) + persona/case-book (learn-from-every-convo).
# learnings.jsonl = Lumen's durable accumulated memory; personas = sealed persona + case-book (was unbacked).
PATHS_TO_BACKUP+=("/home/lumen/minerva/learnings.jsonl")
PATHS_TO_BACKUP+=("/home/lumen/personas")

# Lumen Plays Pokemon — wq#566, Frank-approved option B (2026-08-14). checkpoints = the
# ONLY copy of 123h of campaign progress; hourly tier is deliberate. events/ goes to the
# DAILY tier (backup-important.sh) because .state.gz files are gzip-compressed: restic's
# chunker shares NOTHING across them (measured, wq#556 correction), so every new
# checkpoint costs its full ~500KB — ~20MB/day hourly churn, acceptable; events at 27M
# would not be. roms/ EXCLUDED by omission on purpose: re-obtainable, and Frank does not
# want ROM images in the offsite repo (the offsite leg copies whatever this repo holds).
PATHS_TO_BACKUP+=("/home/lumen/pokemon/checkpoints")
PATHS_TO_BACKUP+=("/home/lumen/pokemon/saves")
PATHS_TO_BACKUP+=("/home/lumen/pokemon/runtime")
# wq#1910 (despag P2-1 AC5): the harness gateway's DESPAG_DATA_DIR — control.db holds session
# leases + idempotency reservations (despag/app.py:106-107) and blobs/ holds checkpoints;
# tiny, hourly tier so a disk failure cannot lose in-flight gateway state.
PATHS_TO_BACKUP+=("/home/lumen/despag-data")

EXCLUDES=(
    --exclude "/home/lumen/scratch/research/cache"
    --exclude "/home/lumen/scratch/research_dupes_quarantine_*"
    --exclude "/home/lumen/scratch/*.tmp"
    --exclude "/home/lumen/scratch/__pycache__"
    --exclude "*.pyc"
    --exclude "/home/lumen/.secrets/gate-snapshots"
    --exclude "/home/lumen/beszel/beszel_data/data.db*"
    --exclude "/home/lumen/beszel/beszel_data/auxiliary.db*"
    # wq#5245: the gate corpus hold-out half is root:root 0700 BY DESIGN
    # (scratch/gate_corpus/run_corpus.py:43 "root-owned 0700") so no lumen-uid
    # agent can read it. Since 2026-11-18 restic (running as lumen) hit EACCES on
    # it every hour, exited 3 ("at least one source file could not be read") and
    # the run failed with errors=2 though snapshots were saved. Copying it into a
    # lumen-readable repo would also void the hold-out, so it stays out of restic.
    --exclude "/home/lumen/scratch/gate_corpus/.hidden"
)

# wq#669: check for lock contention before each repo. If the prune is holding
# the lock, skip that repo with a clean exit rather than burning the entire
# --retry-lock budget. The next hourly run picks it up — data is at most ~1h
# stale, same tier the retry-lock design already accepts. A backup that times
# out silently is a data-loss risk; a backup that skips loudly is not.
LOCK_WAIT_CAP=120   # seconds to wait for a contended lock before skipping

for REPO in "$REPO_LOCAL" "$REPO_MIRROR"; do
    log "=== restic backup -> $REPO ==="
    # Pre-check: is a lock held by another process (e.g. weekly prune)?
    if locks=$("$RESTIC_BINARY" -r "$REPO" list locks 2>>"$LOG") && [ -n "$locks" ]; then
        log "  lock contention detected on $REPO — another process (prune?) holds the lock"
        log "  using short retry (${LOCK_WAIT_CAP}s) instead of full 50m budget"
        if "$RESTIC_BINARY" -r "$REPO" --retry-lock "${LOCK_WAIT_CAP}s" backup \
            --tag critical --tag "host=lumen" --tag "ts=$TS" \
            "${EXCLUDES[@]}" \
            "${PATHS_TO_BACKUP[@]}" >>"$LOG" 2>&1; then
            log "  OK (lock cleared within ${LOCK_WAIT_CAP}s)"
        else
            log "  SKIPPED $REPO — lock not released within ${LOCK_WAIT_CAP}s, deferring to next hourly run"
            # NOT incrementing ERRORS: this is an expected contention, not a failure.
            # The next hourly run will succeed. Incrementing ERRORS here would fire
            # the aq alert for a condition that self-heals in <=1h.
        fi
    else
        if "$RESTIC_BINARY" -r "$REPO" --retry-lock 50m backup \
            --tag critical --tag "host=lumen" --tag "ts=$TS" \
            "${EXCLUDES[@]}" \
            "${PATHS_TO_BACKUP[@]}" >>"$LOG" 2>&1; then
            log "  OK"
        else
            rc=$?
            # wq#5245: name the restic exit code so the journal says WHY (3 = snapshot
            # saved but some source unreadable; restic's errors themselves are in $LOG).
            log "  FAILED restic backup to $REPO (restic rc=$rc; details in $LOG)"; ERRORS=$((ERRORS+1))
        fi
    fi
done

log "=== DONE — errors=$ERRORS ==="
exit $ERRORS
