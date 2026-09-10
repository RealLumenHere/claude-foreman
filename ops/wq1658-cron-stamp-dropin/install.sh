#!/usr/bin/env bash
# wq#1658 — deploy the cron-stamp drop-in generator and wire it into
# `unit-drift snapshot`. Idempotent: safe to re-run.
#
# Does NOT run `lumen-cron-stamp-dropin-gen apply` against the live fleet —
# that writes a 99-cron-stamp.conf drop-in for every one of the ~122
# currently-uncovered fleet timers in one shot (every backup, vault HA
# monitor, sentinel-probe, model-watchdog, drift guard) plus one
# `systemctl daemon-reload`. That is deliberately a separate, explicit
# step (see README.md "Deploy") rather than bundled into install — same
# reasoning wq1184's install.sh used for not auto-enabling lumen-gatus.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
BIN=/home/lumen/bin

echo "== installing lumen-cron-stamp-dropin-gen =="
cp "$SRC/lumen_cron_stamp_dropin_gen.py" "$BIN/lumen-cron-stamp-dropin-gen"
chmod 755 "$BIN/lumen-cron-stamp-dropin-gen"

echo "== dry-run plan against current fleet state =="
python3 "$BIN/lumen-cron-stamp-dropin-gen" plan

cat <<'EOF'

Next steps (manual, by design — see README.md):
  1. Patch /home/lumen/bin/unit-drift: cmd_snapshot() should call
     `subprocess.run(["/home/lumen/bin/lumen-cron-stamp-dropin-gen", "apply"])`
     as its first line, guarded so a missing/failing generator can't break
     the existing snapshot capture.
  2. Splice handoff-verify-snippet.sh into /home/lumen/bin/handoff's
     verify() function.
  3. Run `lumen-cron-stamp-dropin-gen apply` as root once, by hand, to land
     the initial coverage jump; `unit-drift snapshot` keeps it there after.
EOF
