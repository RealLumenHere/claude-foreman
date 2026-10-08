#!/usr/bin/env bash
# wq#3650: Mac Pro WoL integration — wake-on-demand dispatch + auto-sleep.
# Run as root ON isis or hathor, from a copy of this dir (same shape as
# ops/wq2472-vlogs-fleet-rollout/install.sh). Idempotent.
#
# Installs, on the target Mac Pro only:
#   - macpro-wol-enable.service  (ethtool wol g, every boot)
#   - macpro-no-turbo.service    (turbo boost off, every boot)
#   - 10-wq3650-threads.conf     (OLLAMA_NUM_THREADS=12, ollama.service.d/ drop-in)
#   - macpro-autosleep.timer/.service (load<0.2 for 30min -> clean poweroff)
#
# Does NOT touch GPU power state or the fan curve: those are already live via
# ops/wq2166-gpu-lowpower-ordering and ops/mbpfan (wq#2508) on both isis and hathor. See
# README.md for the full picture and why this bundle does not re-ship them.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

# wq#1696 deploy-verify contract (ops/VERIFY_CONTRACT.md). Read-only, before the root check, so a
# liveness timer can run it as the lumen user. Lumen-local only: there is no local target for a
# Mac Pro bundle (ops/VERIFY_CONTRACT.md "Bundles that do not install onto Lumen"), so this asks
# isis and hathor directly. An unreachable node is ERR, never MISS.
if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh"
  for h in isis hathor; do
    vremote_up "$h" || continue
    vremote_file "$h" "$HERE/macpro-wol-enable.service" /etc/systemd/system/macpro-wol-enable.service
    vremote_file "$h" "$HERE/macpro-wol-enable.sh"       /usr/local/sbin/macpro-wol-enable.sh
    vremote_unit "$h" macpro-wol-enable.service
    vremote_file "$h" "$HERE/macpro-no-turbo.service"    /etc/systemd/system/macpro-no-turbo.service
    vremote_file "$h" "$HERE/macpro-no-turbo.sh"         /usr/local/sbin/macpro-no-turbo.sh
    vremote_unit "$h" macpro-no-turbo.service
    vremote_file "$h" "$HERE/10-wq3650-threads.conf" \
                      /etc/systemd/system/ollama.service.d/10-wq3650-threads.conf
    vremote_file "$h" "$HERE/macpro-autosleep.timer"     /etc/systemd/system/macpro-autosleep.timer
    vremote_file "$h" "$HERE/macpro-autosleep.service"   /etc/systemd/system/macpro-autosleep.service
    vremote_file "$h" "$HERE/macpro-autosleep.sh"        /usr/local/sbin/macpro-autosleep.sh
    vremote_unit "$h" macpro-autosleep.timer
  done
  vdone
fi

[ "$(id -u)" = 0 ] || { echo "must run as root"; exit 1; }
case "$(hostname -s)" in
  isis|hathor) ;;
  *) echo "REFUSING: $(hostname -s) is not isis or hathor (wq#3650 scope)"; exit 1 ;;
esac
command -v ethtool >/dev/null || { echo "REFUSING: ethtool not installed"; exit 1; }

install -D -m 0755 "$HERE/macpro-wol-enable.sh" /usr/local/sbin/macpro-wol-enable.sh
install -D -m 0644 "$HERE/macpro-wol-enable.service" /etc/systemd/system/macpro-wol-enable.service
install -D -m 0755 "$HERE/macpro-no-turbo.sh" /usr/local/sbin/macpro-no-turbo.sh
install -D -m 0644 "$HERE/macpro-no-turbo.service" /etc/systemd/system/macpro-no-turbo.service
install -D -m 0644 "$HERE/10-wq3650-threads.conf" /etc/systemd/system/ollama.service.d/10-wq3650-threads.conf
install -D -m 0755 "$HERE/macpro-autosleep.sh" /usr/local/sbin/macpro-autosleep.sh
install -D -m 0644 "$HERE/macpro-autosleep.service" /etc/systemd/system/macpro-autosleep.service
install -D -m 0644 "$HERE/macpro-autosleep.timer" /etc/systemd/system/macpro-autosleep.timer

systemctl daemon-reload
systemctl enable --now macpro-wol-enable.service
systemctl enable --now macpro-no-turbo.service
systemctl enable --now macpro-autosleep.timer
systemctl try-restart ollama.service   # pick up OLLAMA_NUM_THREADS; no-op if ollama is not installed yet

/usr/local/sbin/macpro-wol-enable.sh --check
/usr/local/sbin/macpro-no-turbo.sh --check
# No unit-drift accept here: that reconciler tracks Lumen's own /etc/systemd/system, not other
# fleet nodes' (ops/VERIFY_CONTRACT.md "Bundles that do not install onto Lumen") — this bundle's
# own `install.sh --verify` (run from Lumen via vremote_unit) is the drift check for isis/hathor.
echo "installed on $(hostname -s)"
