# Sourced by all backup scripts and systemd units (via EnvironmentFile).
# DO NOT add secrets here — RESTIC_PASSWORD lives only in .restic-password.
export RESTIC_BINARY=/home/lumen/.local/bin/restic
export RESTIC_PASSWORD_FILE=/home/lumen/services/backup/.restic-password
export REPO_LOCAL=/mnt/lumen-backup/restic-local
export REPO_MIRROR=/data/lumen/backups
export LOG_DIR=/home/lumen/services/backup/logs
export STAGE_DIR=/scratch/backup-stage

# 2026-07-19 (claude-web-0718-burn): pin restic cache off tmpfs. Without this,
# sandboxed/temp-HOME runs fall back to "temporary cache in /tmp/restic-check-
# cache-*" on tmpfs — interrupted checks leaked 21.7G into /tmp (=RAM) on
# 07-19, amplifying the memory-pressure incident. /scratch is md1 RAID0 scratch.
export RESTIC_CACHE_DIR=/scratch/restic-cache

# wq#3717: path to the MongoDB backup credentials (600, same convention as
# RESTIC_PASSWORD_FILE above) — not the secret itself, see instruction not
# to add secrets to this file.
export MONGO_CREDENTIALS_FILE=/home/lumen/services/backup/.mongo-credentials
