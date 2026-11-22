#!/bin/bash
# wq#1652 — install cfd-actuator, the single cloudflared restart owner.
# Run as root via `lumen-run --root` (deploy step, NOT run by the build lane).
# Does not touch cloudflared itself; it only adds the drain timer + sudoers.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# wq#1696 deploy-verify contract (wq#2985). Read-only, before any install step.
if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/cfd_actuator.py" /home/lumen/bin/cfd-actuator
  vunit_file "$SRC/lumen-cfd-actuator.service" \
             /etc/systemd/system/lumen-cfd-actuator.service
  vunit_file "$SRC/lumen-cfd-actuator.timer" \
             /etc/systemd/system/lumen-cfd-actuator.timer
  venabled   lumen-cfd-actuator.timer
  vactive    lumen-cfd-actuator.timer
  vtimer_scheduled lumen-cfd-actuator.timer  # wq#4910: vactive also passes SubState=elapsed
  [ -e /etc/sudoers.d/lumen-cfd-actuator ] \
    || _vmiss "absent: /etc/sudoers.d/lumen-cfd-actuator"
  vdone
fi

echo "== script -> /home/lumen/bin/cfd-actuator =="
install -o lumen -g lumen -m 755 "$SRC/cfd_actuator.py" /home/lumen/bin/cfd-actuator
install -d -o lumen -g lumen -m 755 /home/lumen/state /home/lumen/state/cfd-actuator

echo "== sudoers: exactly the one action cmd_drain runs =="
cat > /etc/sudoers.d/lumen-cfd-actuator <<'EOF'
# wq#1652 cfd-actuator: the ONE path allowed to bounce cloudflared.
# Byte-identical to RESTART_CMD in cfd_actuator.py; change both together.
# Detached systemd-run (instinct 5), no --unit because sudoers rejects
# wildcards in command arguments (ISSUE-LOG #46). No HUP grant: SIGHUP
# kills cloudflared and systemd does not restart it (wq#3575).
lumen ALL=(root) NOPASSWD: /usr/bin/systemd-run --collect /usr/bin/systemctl restart cloudflared.service
EOF
chmod 440 /etc/sudoers.d/lumen-cfd-actuator
visudo -cf /etc/sudoers.d/lumen-cfd-actuator

echo "== units =="
install -m 644 "$SRC/lumen-cfd-actuator.service" /etc/systemd/system/lumen-cfd-actuator.service
install -m 644 "$SRC/lumen-cfd-actuator.timer" /etc/systemd/system/lumen-cfd-actuator.timer
systemctl daemon-reload
systemctl enable --now lumen-cfd-actuator.timer
systemctl list-timers lumen-cfd-actuator.timer --no-pager || true

echo "== smoke: read-only status (a filed request would restart the tunnel) =="
sudo -u lumen /home/lumen/bin/cfd-actuator status

echo "== installed. Next: migrate callers per README rollout stages. =="
