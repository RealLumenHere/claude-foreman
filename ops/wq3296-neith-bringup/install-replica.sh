#!/usr/bin/env bash
# wq#3296 — run ON NEITH as root, from a copy of this kit dir. Installs the
# fleet-memory replica (role 2): dirs, restic repo init, pull + restore-test
# units. Prereqs: restic, sqlite3, rsync installed; root has BatchMode ssh to
# the Lumen account named in neith-memory-pull.sh (verify the IP against
# `lumen_state.py current lumen` first — never assume it hasn't changed).
set -euo pipefail
test "$(id -u)" = 0 || { echo "run as root" >&2; exit 2; }
KIT=$(dirname "$(readlink -f "$0")")
ROOT=/srv/fleet-memory

command -v restic >/dev/null || { echo "restic not installed" >&2; exit 2; }
command -v sqlite3 >/dev/null || { echo "sqlite3 not installed" >&2; exit 2; }

mkdir -p "$ROOT/current" "$ROOT/status"
chmod 750 "$ROOT"

# repo password: generated here, 0600, never committed, never printed
if ! test -s "$ROOT/.restic-pass"; then
  (umask 077; head -c 32 /dev/urandom | base64 > "$ROOT/.restic-pass")
fi
if ! test -s "$ROOT/restic/config"; then
  RESTIC_PASSWORD_FILE="$ROOT/.restic-pass" restic -r "$ROOT/restic" init
fi

install -m 755 "$KIT/neith-memory-pull.sh" /usr/local/sbin/neith-memory-pull.sh
install -m 755 "$KIT/neith-restic-restore-test.sh" /usr/local/sbin/neith-restic-restore-test.sh
install -m 644 "$KIT/neith-memory-pull.service" "$KIT/neith-memory-pull.timer" \
  "$KIT/neith-restic-restore-test.service" "$KIT/neith-restic-restore-test.timer" \
  /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now neith-memory-pull.timer neith-restic-restore-test.timer

echo "installed. First verification (run now):"
echo "  systemctl start neith-memory-pull.service && cat $ROOT/status/pull.json"
echo "  systemctl start neith-restic-restore-test.service && cat $ROOT/status/restore-test.json"
