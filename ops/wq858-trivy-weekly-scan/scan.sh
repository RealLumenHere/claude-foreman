#!/usr/bin/env bash
# wq#858 — Weekly Trivy CVE scan: all local Docker images + host rootfs.
# Runs as lumen via systemd oneshot. Reports land in ./reports/.
set -euo pipefail

TRIVY=/home/lumen/bin/trivy
REPORT_DIR=/home/lumen/services/trivy-weekly/reports
STAMP=$(date -u +%Y%m%d_%H%M%S)
REPORT="$REPORT_DIR/trivy_${STAMP}.txt"
JSON_REPORT="$REPORT_DIR/trivy_${STAMP}.json"
NTFY_TOPIC=lumen-fleet
# wq#2858: never walk worktree/archive trees (semaphore deadline = FATAL since 08-23).
# Covers /home/lumen/worktrees and its /data/lumen/lumen-home/worktrees alias.
SKIP_DIRS=/home/lumen/.claude,/home/lumen/.cache,/home/lumen/.npm,/home/lumen/.cargo,/home/lumen/backups,/home/lumen/worktrees,/home/lumen/scratch,/data/lumen,/scratch,/mnt,/proc,/sys,/dev,/run,/tmp,/var/lib/docker
FAILED=0

echo "=== Trivy weekly scan $STAMP ===" | tee "$REPORT"
echo "" >> "$REPORT"

# --- Update DB first ---
echo "[*] Updating Trivy DB..." | tee -a "$REPORT"
$TRIVY image --download-db-only --quiet 2>&1 | tee -a "$REPORT" || true

# --- Scan all local Docker images ---
echo "" >> "$REPORT"
echo "=== CONTAINER IMAGE SCAN ===" | tee -a "$REPORT"
IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -v '<none>' | sort -u)
TOTAL_HIGH=0
TOTAL_CRIT=0
for img in $IMAGES; do
  echo "[*] Scanning $img ..." | tee -a "$REPORT"
  $TRIVY image --severity HIGH,CRITICAL --format table --quiet "$img" 2>&1 | tee -a "$REPORT" || true
done

# --- Scan host rootfs ---
echo "" >> "$REPORT"
echo "=== HOST ROOTFS SCAN ===" | tee -a "$REPORT"
$TRIVY rootfs --scanners vuln --timeout 45m --skip-dirs "$SKIP_DIRS" --severity HIGH,CRITICAL --format table --quiet / 2>&1 | tee -a "$REPORT" || true

# --- JSON summary for machine consumption ---
echo "" >> "$REPORT"
echo "=== JSON SUMMARY ===" | tee -a "$REPORT"
$TRIVY rootfs --scanners vuln --timeout 45m --skip-dirs "$SKIP_DIRS" --severity HIGH,CRITICAL --format json --quiet / > "$JSON_REPORT" 2> "$JSON_REPORT.err" || true
# wq#2858: a scan that produced no valid report is a FAILURE, not "clean".
if ! python3 -c 'import json,sys,os; p=sys.argv[1]; assert os.path.getsize(p)>=2048; d=json.load(open(p)); assert "Results" in d or "ArtifactName" in d' "$JSON_REPORT" 2>/dev/null; then
  FAILED=1
  echo "!!! TRIVY SCAN FAILED: JSON report missing/<2KB/unparseable ($JSON_REPORT; stderr in $JSON_REPORT.err)" | tee -a "$REPORT"
fi

# --- Count findings ---
HIGH_COUNT=$(grep -c "HIGH" "$REPORT" 2>/dev/null || echo 0)
CRIT_COUNT=$(grep -c "CRITICAL" "$REPORT" 2>/dev/null || echo 0)

echo "" >> "$REPORT"
echo "=== SUMMARY ===" | tee -a "$REPORT"
echo "HIGH findings (approx): $HIGH_COUNT" | tee -a "$REPORT"
echo "CRITICAL findings (approx): $CRIT_COUNT" | tee -a "$REPORT"
echo "Full report: $REPORT" | tee -a "$REPORT"
echo "JSON report: $JSON_REPORT" | tee -a "$REPORT"

# --- Prune reports older than 90 days ---
find "$REPORT_DIR" -name 'trivy_*.txt' -mtime +90 -delete 2>/dev/null || true
find "$REPORT_DIR" -name 'trivy_*.json' -mtime +90 -delete 2>/dev/null || true

# --- Notify ---
# wq#5239: the ntfy password never goes on curl's argv (/proc/<pid>/cmdline is readable
# by every local user). It is read into an unexported shell variable and handed to curl
# as a config file on stdin (-K -); printf is a bash builtin, so no process argv holds it.
# Config quoted-string escapes: backslash and double quote.
ntfy_send() {  # ntfy_send TITLE PRIORITY TAGS BODY
  local pass
  pass="$(grep -m1 NTFY_PASS /home/lumen/agent/.env 2>/dev/null | cut -d= -f2-)" || true
  pass="${pass//\\/\\\\}"
  pass="${pass//\"/\\\"}"
  printf 'user = "lumen:%s"\n' "$pass" | curl -s -K - \
    -H "Title: $1" -H "Priority: $2" -H "Tags: $3" -d "$4" \
    "http://127.0.0.1:8080/$NTFY_TOPIC" 2>/dev/null || true
}

if [ "$FAILED" -eq 1 ]; then
  ntfy_send "Trivy weekly scan FAILED (no valid report)" high rotating_light \
    "No valid JSON report. See $REPORT and $JSON_REPORT.err"
  echo "[!] Done with FAILURE."; exit 1
elif [ "$CRIT_COUNT" -gt 0 ]; then
  ntfy_send "Trivy: $CRIT_COUNT CRITICAL CVEs found" high warning \
    "Weekly scan found $CRIT_COUNT CRITICAL and $HIGH_COUNT HIGH. Report: $REPORT"
else
  ntfy_send "Trivy weekly: $HIGH_COUNT HIGH, 0 CRITICAL" default white_check_mark \
    "Clean scan. Report: $REPORT"
fi

echo "[*] Done."
