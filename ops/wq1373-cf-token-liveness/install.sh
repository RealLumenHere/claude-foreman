#!/usr/bin/env bash
# wq#1373 — install cf-token-liveness. Run as root via `lumen-run --root`
# (repo convention: ops/wq1184-gatus-health-checks, ops/wq1252-lumen-public-guardian).
# Idempotent: safe to re-run after every `git pull` in foreman.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/lumen/services/cf-token-liveness

echo "== code -> $DEST =="
install -d -o lumen -g lumen -m 755 "$DEST"
for f in cf_token_liveness.py tokens.json; do
  install -o lumen -g lumen -m 644 "$SRC/$f" "$DEST/$f"
done
chmod 755 "$DEST/cf_token_liveness.py"

echo "== dry-run selftest before enabling =="
sudo -u lumen /usr/bin/python3 "$DEST/cf_token_liveness.py" --dry-run

echo "== systemd units =="
install -m 644 "$SRC/cf-token-liveness.service" /etc/systemd/system/cf-token-liveness.service
install -m 644 "$SRC/cf-token-liveness.timer" /etc/systemd/system/cf-token-liveness.timer
systemctl daemon-reload

echo "== enable =="
systemctl enable --now cf-token-liveness.timer
systemctl list-timers cf-token-liveness.timer --no-pager || true

cat <<EOF

== installed ==
Next pass runs within 5 minutes, then hourly. Watch it:
  journalctl -u cf-token-liveness -f
  python3 $DEST/cf_token_liveness.py --dry-run

tokens.json currently covers only the two mailbot D1 tokens (verified real
files at install time). Add an entry per remaining fleet CF token (Access-mgmt
i.e. the #37 token, Workers, DNS, R2 — see README.md) as their token_file
paths are confirmed; each is a JSON object, no code change required.
EOF
