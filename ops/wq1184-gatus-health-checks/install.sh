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
# The ntfy relay IS enabled here (inert until Gatus calls it). The cron
# dead-man timer is installed but left disabled unless
# LUMEN_CRON_DEADMAN_ENABLE=1: the 2026-10-08 subtraction turned it off on
# purpose (wq#5250).
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
GATUS_VERSION="v5.36.0"
GATUS_BUILD_DIR="/home/lumen/build/gatus-${GATUS_VERSION}"
KB_GATUS_DIR="/home/lumen/kb/gatus"
# wq#1483: LITELLM_MASTER_KEY must NOT live under kb/gatus -- kb is a base fworker
# add_dir (agents.yaml default add_dirs), so anything there is worker-readable by
# every worker regardless of file mode. The key lives under /home/lumen/.secrets/
# instead; only config.yaml (no secret) stays under kb/gatus.
SECRETS_ENV="/home/lumen/.secrets/gatus-litellm-master.env"

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
# Two things this must NOT do, both of which would make it lie:
#   * lumen-gatus.service is installed but deliberately not enabled (it would
#     false-page before LITELLM_MASTER_KEY is set), so only the relay and the
#     dead-man are checked for enablement.
#   * $SECRETS_ENV is checked for existence only, never read -- it holds a key.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/config.yaml"  "$KB_GATUS_DIR/config.yaml"
  vfile      "$SRC/ntfy_relay.py" /home/lumen/bin/gatus-ntfy-relay
  vfile      "$SRC/cron_stamp.sh" /home/lumen/bin/lumen-cron-stamp
  vunit_file "$SRC/gatus.service" /etc/systemd/system/lumen-gatus.service
  vunit_file "$SRC/gatus-ntfy-relay.service" \
             /etc/systemd/system/lumen-gatus-ntfy-relay.service
  vunit_file "$SRC/lumen-cron-deadman.service" \
             /etc/systemd/system/lumen-cron-deadman.service
  vunit_file "$SRC/lumen-cron-deadman.timer" \
             /etc/systemd/system/lumen-cron-deadman.timer
  venabled   lumen-gatus-ntfy-relay.service
  # wq#5250: no venabled/vactive/vtimer_scheduled for lumen-cron-deadman.timer. It is
  # intended-off: the 2026-10-08 subtraction disabled it on purpose
  # (/home/lumen/scratch/subtraction/timers-disable-list.txt:78, REPORT.md:67
  # "watchers-of-watchers"). Asserting enabled+active made --verify fail permanently, and
  # the ops-root-apply it filed told the reader to run this script as root, which (before
  # wq#5250) ran `systemctl enable --now` and undid the subtraction. The unit files are
  # still checked above. Re-enabling it is a decision, not a heal; whoever makes it sets
  # LUMEN_CRON_DEADMAN_ENABLE=1 below and adds venabled/vactive/vtimer_scheduled back here.
  [ -e "$SECRETS_ENV" ] || _vmiss "absent: $SECRETS_ENV"
  vdone
fi

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
if [ ! -f "$SECRETS_ENV" ]; then
  sudo -u lumen bash -c "printf 'LITELLM_MASTER_KEY=\n' > '$SECRETS_ENV'"
  chmod 600 "$SECRETS_ENV"
  echo "!! set LITELLM_MASTER_KEY in $SECRETS_ENV before enabling lumen-gatus.service"
fi

echo "== ntfy relay + cron-stamp helper =="
sudo -u lumen "$SRC/../wq2856-atomic-install/install-script" -m 0755 "$SRC/ntfy_relay.py" /home/lumen/bin/gatus-ntfy-relay
sudo -u lumen "$SRC/../wq2856-atomic-install/install-script" -m 0755 "$SRC/cron_stamp.sh" /home/lumen/bin/lumen-cron-stamp

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

echo "== enabling relay (no false-alert risk on install) =="
systemctl enable --now lumen-gatus-ntfy-relay.service
# wq#5250: the cron dead-man timer is intended-off since the 2026-10-08 subtraction
# (see the --verify block). A re-run of this script must not silently re-enable it;
# opt in explicitly with LUMEN_CRON_DEADMAN_ENABLE=1.
if [ "${LUMEN_CRON_DEADMAN_ENABLE:-0}" = 1 ]; then
  systemctl enable --now lumen-cron-deadman.timer
else
  echo "   lumen-cron-deadman.timer installed, left disabled (intended-off, wq#5250)"
fi

cat <<'EOF'

== lumen-gatus.service installed but NOT enabled ==
1. Set LITELLM_MASTER_KEY in /home/lumen/.secrets/gatus-litellm-master.env (0600,
   already created with an empty value).
2. Review /home/lumen/kb/gatus/config.yaml against the HARD BRIDGE RULES in
   its header, especially if you edit it later.
3. systemctl enable --now lumen-gatus.service
4. Verify: curl -s http://127.0.0.1:8783/api/v1/endpoints/statuses | jq .
See RUNBOOK.md for the full checklist and rollback.
EOF
