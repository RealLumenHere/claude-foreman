#!/usr/bin/env bash
# wq#3282 thoth-arr-ensure install. Runs ON thoth as a user with sudo:
#   scp -r ops/wq3282-thoth-arr-ensure thoth@100.75.1.108:/tmp/ && ssh thoth@100.75.1.108 'sudo bash /tmp/wq3282-thoth-arr-ensure/install.sh'
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)

if [ "${1:-}" = --verify ]; then
  . "$HERE/../_verify.sh" 2>/dev/null || . "$HERE/_verify.sh"
  if [ "$(hostname)" = thoth ]; then
    vfile "$HERE/thoth-arr-ensure" /usr/local/sbin/thoth-arr-ensure
    vunit_file "$HERE/thoth-arr-ensure.service" /etc/systemd/system/thoth-arr-ensure.service
    vunit_file "$HERE/thoth-arr-ensure.timer" /etc/systemd/system/thoth-arr-ensure.timer
    venabled thoth-arr-ensure.timer
    vactive thoth-arr-ensure.timer
  else
    # payload lives on thoth: check it there (ops/VERIFY_CONTRACT.md, unreachable = ERR not MISS)
    H=thoth@100.75.1.108
    vremote_up "$H" || vdone
    vremote_file "$H" "$HERE/thoth-arr-ensure" /usr/local/sbin/thoth-arr-ensure
    vremote_file "$H" "$HERE/thoth-arr-ensure.service" /etc/systemd/system/thoth-arr-ensure.service
    vremote_file "$H" "$HERE/thoth-arr-ensure.timer" /etc/systemd/system/thoth-arr-ensure.timer
    vremote_unit "$H" thoth-arr-ensure.timer
  fi
  vdone
fi

[ "$(hostname)" = thoth ] || { echo "refusing: this bundle is for thoth, not $(hostname)"; exit 1; }
install -m 0755 -o root -g root "$HERE/thoth-arr-ensure" /usr/local/sbin/thoth-arr-ensure
install -m 0644 -o root -g root "$HERE/thoth-arr-ensure.service" /etc/systemd/system/thoth-arr-ensure.service
install -m 0644 -o root -g root "$HERE/thoth-arr-ensure.timer" /etc/systemd/system/thoth-arr-ensure.timer
systemctl daemon-reload
systemctl enable --now thoth-arr-ensure.timer
echo "--- verify ---"
systemctl list-timers thoth-arr-ensure.timer --no-pager --no-legend
echo "canary: bash $HERE/canary.sh"
echo "pause:  touch /etc/thoth-arr-ensure.pause   (rm it to resume)"
echo "rollback: systemctl disable --now thoth-arr-ensure.timer; rm /etc/systemd/system/thoth-arr-ensure.{service,timer} /usr/local/sbin/thoth-arr-ensure; systemctl daemon-reload"
