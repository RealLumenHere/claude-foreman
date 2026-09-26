#!/usr/bin/env bash
# wq#2508 rollout: install mbpfan 2.4.0 (distro) + ops/mbpfan/mbpfan.conf on one MacPro6,1 and
# assert it actually took the fan (fan1_manual=1). Usage: rollout.sh <ssh-alias>
set -uo pipefail
N=${1:?node}
CONF=/home/lumen/foreman/ops/mbpfan/mbpfan.conf
SSH="ssh -o BatchMode=yes $N"
$SSH 'sudo mkdir -p /var/backups/wq2508; [ -e /etc/mbpfan.conf ] && sudo cp -a /etc/mbpfan.conf /var/backups/wq2508/mbpfan.conf.bak.$(date +%Y%m%d_%H%M%S); true' || exit 1
scp -q "$CONF" "$N":/tmp/mbpfan.conf || exit 1
# policy-rc.d 101: stop the package autostarting on the distro default conf before ours lands.
# NEEDRESTART_MODE=l: needrestart only lists, never bounces services (isis serves llama-qwen36).
$SSH 'printf "#!/bin/sh\nexit 101\n" | sudo tee /usr/sbin/policy-rc.d >/dev/null && sudo chmod 755 /usr/sbin/policy-rc.d; sudo NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o Dpkg::Options::=--force-confold mbpfan >/dev/null; rc=$?; sudo rm -f /usr/sbin/policy-rc.d; exit $rc' || { echo "$N FAIL apt"; exit 1; }
$SSH 'sudo install -m644 /tmp/mbpfan.conf /etc/mbpfan.conf && sudo systemctl enable --now mbpfan >/dev/null 2>&1' || { echo "$N FAIL enable"; exit 1; }
sleep 8
out=$($SSH 'd=/sys/devices/platform/applesmc.768; echo "$(dpkg-query -W -f="\${Version}" mbpfan) $(systemctl is-active mbpfan) $(systemctl is-enabled mbpfan) manual=$(cat $d/fan1_manual) out=$(cat $d/fan1_output) in=$(cat $d/fan1_input) failed=$(systemctl --failed --no-legend | wc -l)"')
echo "$N $out"
case "$out" in *" active enabled manual=1 "*) echo "$N PASS" ;; *) echo "$N FAIL: mbpfan not driving the fan"; exit 1 ;; esac
