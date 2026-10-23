#!/usr/bin/env bash
# wq#4487 — install and enable fail2ban on Horus.
#
# Run ON Horus as root (this worker's sandbox has no exec grant for Horus —
# see /home/lumen/kb/ISSUE-LOG.md wq#4487 entry):
#
#   sudo bash install.sh /path/to/this/dir
#
# Additive-only: installs a package and enables a service, does not touch
# live sshd_config or UFW rules, so unlike wq#4485/wq#3108 this does not need
# a dead-man rollback — the only failure mode is an overly aggressive ban,
# which jail.local's ignoreip (tailnet + loopback) already guards against.
set -euo pipefail

dir="${1:?usage: install.sh <dir-containing-jail.local>}"
test -f "$dir/jail.local" || { echo "no jail.local in $dir" >&2; exit 2; }

apt-get update
apt-get install -y fail2ban

install -m 0644 "$dir/jail.local" /etc/fail2ban/jail.local

systemctl enable --now fail2ban
systemctl restart fail2ban

echo "fail2ban status:"
systemctl is-active fail2ban
fail2ban-client status sshd
