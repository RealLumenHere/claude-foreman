#!/usr/bin/env bash
# wq#1184 — stand up the fleet-hardening Gatus instance + ntfy relay + cron
# dead-man's switch. Run as root via `lumen-run --root` (repo convention —
# see ops/wq900-primary-tree-guard, ops/wq901-ntfy-oob-429, ops/wq911-auth-alerter).
#
# Builds Gatus from source (go build, no docker — upstream ships no binary
# releases, only a docker image, so `go build` is the only "single Go
# binary, no docker" path). Idempotent: safe to re-run.
#
# lumen-gatus.service itself is installed but NOT enabled here — it starts
# probing LITELLM's authenticated /health endpoint immediately, and
# LITELLM_MASTER_KEY has no safe default, so auto-enabling would false-page
# on first boot. Enable it by hand after setting the key (see RUNBOOK.md).
# The ntfy relay and cron dead-man ARE enabled here: neither can produce a
# false alert on install (the relay is inert until Gatus calls it; the
# dead-man only alerts once a stale stamp exists, and none do yet).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
GATUS_VERSION="v5.36.0"
GATUS_BUILD_DIR="/home/lumen/build/gatus-${GATUS_VERSION}"
KB_GATUS_DIR="/home/lumen/kb/gatus"

echo "== building gatus ${GATUS_VERSION} (go build, no docker) =="
sudo -u lumen mkdir -p "$(dirname "$GATUS_BUILD_DIR")"
if [ ! -d "$GATUS_BUILD_DIR/.git" ]; then
  sudo -u lumen git clone --branch "$GATUS_VERSION" --depth 1 \
    https://github.com/TwiN/gatus.git "$GATUS_BUILD_DIR"
fi
sudo -u lumen bash -c "cd '$GATUS_BUILD_DIR' && CGO_ENABLED=1 go build -o /home/lumen/bin/gatus ."
chmod 755 /home/lumen/bin/gatus
/home/lumen/bin/gatus --version || true

echo "== kb config dir =="
sudo -u lumen mkdir -p "$KB_GATUS_DIR/data"
sudo -u lumen cp "$SRC/config.yaml" "$KB_GATUS_DIR/config.yaml"
if [ ! -f "$KB_GATUS_DIR/gatus.env" ]; then
  sudo -u lumen bash -c "printf 'LITELLM_MASTER_KEY=\n' > '$KB_GATUS_DIR/gatus.env'"
  chmod 600 "$KB_GATUS_DIR/gatus.env"
  echo "!! set LITELLM_MASTER_KEY in $KB_GATUS_DIR/gatus.env before enabling lumen-gatus.service"
fi

echo "== ntfy relay + cron-stamp helper =="
sudo -u lumen cp "$SRC/ntfy_relay.py" /home/lumen/bin/gatus-ntfy-relay
chmod 755 /home/lumen/bin/gatus-ntfy-relay
sudo -u lumen cp "$SRC/cron_stamp.sh" /home/lumen/bin/lumen-cron-stamp
chmod 755 /home/lumen/bin/lumen-cron-stamp

echo "== systemd units =="
cp "$SRC/gatus.service" /etc/systemd/system/lumen-gatus.service
cp "$SRC/gatus-ntfy-relay.service" /etc/systemd/system/lumen-gatus-ntfy-relay.service
cp "$SRC/lumen-cron-deadman.service" /etc/systemd/system/lumen-cron-deadman.service
cp "$SRC/lumen-cron-deadman.timer" /etc/systemd/system/lumen-cron-deadman.timer

echo "== stamp hooks on the 3 foreman-owned lumen-* units (wq#1184 dead-man coverage) =="
FOREMAN_UNITS_DIR="$(cd "$SRC/../../dispatcher/systemd" && pwd)"
for svc in lumen-foreman-dispatch lumen-foreman-selfheal lumen-foreman-health; do
  cp "$FOREMAN_UNITS_DIR/$svc.service" "/etc/systemd/system/$svc.service"
done

systemctl daemon-reload

echo "== enabling relay + dead-man (no false-alert risk on install) =="
systemctl enable --now lumen-gatus-ntfy-relay.service
systemctl enable --now lumen-cron-deadman.timer

cat <<'EOF'

== lumen-gatus.service installed but NOT enabled ==
1. Set LITELLM_MASTER_KEY in /home/lumen/kb/gatus/gatus.env (0600, already
   created with an empty value).
2. Review /home/lumen/kb/gatus/config.yaml against the HARD BRIDGE RULES in
   its header, especially if you edit it later.
3. systemctl enable --now lumen-gatus.service
4. Verify: curl -s http://127.0.0.1:8783/api/v1/endpoints/statuses | jq .
See RUNBOOK.md for the full checklist and rollback.
EOF
