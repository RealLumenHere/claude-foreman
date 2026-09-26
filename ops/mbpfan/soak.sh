#!/usr/bin/env bash
# wq#2508 — mbpfan staged soak on khepri (MacPro6,1). Detached via lumen-run.
# Phases: idle(SMC auto) -> load 15m (SMC auto, BEFORE) -> cool 10m -> install mbpfan ->
#         assert fan1_manual=1 -> idle 5m -> load 30m (AFTER) -> idle 10m.
# Log: 10 s samples to $OUT/samples.csv. Abort load if max coretemp >= 90C or a GPU die >= 90C.
set -uo pipefail
N=${N:-khepri}
OUT=${OUT:-/home/lumen/scratch/wq2508}
CONF=/home/lumen/foreman/ops/mbpfan/mbpfan.conf
mkdir -p "$OUT"; CSV=$OUT/samples.csv; EV=$OUT/events.log
SSH="ssh -o BatchMode=yes -o ControlMaster=auto -o ControlPath=/tmp/wq2508-%r@%h -o ControlPersist=2h $N"
PHASE=init; PF=$OUT/phase; echo init > "$PF"
ev(){ echo "$(date -u +%FT%TZ) $*" | tee -a "$EV"; }

PROBE='d=/sys/devices/platform/applesmc.768
cm=0; for f in /sys/devices/platform/coretemp.*/hwmon/hwmon*/temp*_input; do v=$(cat $f); [ $v -gt $cm ] && cm=$v; done
e=$(sudo -n cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo NA)
echo "$(cat $d/fan1_input),$(cat $d/fan1_output),$(cat $d/fan1_manual),$(cat $d/temp98_input),$(cat $d/temp11_input),$(cat $d/temp74_input),$(cat $d/temp25_input),$(cat $d/temp37_input),$cm,$(cut -d" " -f1 /proc/loadavg),$e"'

echo "ts,phase,fan_in,fan_out,fan_manual,Te0T,TC0P,TM1a,TG0D,TG1D,core_max,load1,rapl_uj" > "$CSV"
sampler(){ while :; do r=$($SSH "$PROBE" 2>/dev/null) || r="ERR"; echo "$(date -u +%FT%TZ),$(cat "$PF"),$r" >> "$CSV"; sleep 10; done; }

# watchdog: kill load if too hot (values in millidegrees)
guard(){ while :; do l=$(tail -1 "$CSV"); IFS=, read -r _ _ _ _ _ _ _ _ g0 g1 cm _ _ <<<"$l"
  if [[ "$cm" =~ ^[0-9]+$ ]] && { [ "$cm" -ge 90000 ] || [ "${g0:-0}" -ge 90000 ] || [ "${g1:-0}" -ge 90000 ]; }; then
    ev "ABORT thermal core_max=$cm TG0D=$g0 TG1D=$g1 — killing load"; $SSH 'pkill -f "openssl speed"' ; fi; sleep 10; done; }

hold(){ echo $1 > "$PF"; ev "phase $1 ${2}s"; sleep "$2"; }
load(){ echo $1 > "$PF"; ev "phase $1 ${2}s (openssl speed -multi 24)"; $SSH "openssl speed -multi 24 -seconds $2 -bytes 16384 sha256 >/dev/null 2>&1"; ev "phase $1 load exited rc=$?"; }

sampler & SP=$!; guard & GP=$!
trap 'kill $SP $GP 2>/dev/null; $SSH "pkill -f \"openssl speed\"" 2>/dev/null' EXIT

ev "pre: $($SSH 'apt-cache policy mbpfan | sed -n 2,3p | tr -s " " | paste -sd" "')"
hold idle_auto 300
load load_auto 900
hold cool_auto 600

echo install > "$PF"
$SSH 'sudo mkdir -p /var/backups/wq2508; [ -e /etc/mbpfan.conf ] && sudo cp -a /etc/mbpfan.conf /var/backups/wq2508/mbpfan.conf.bak.$(date +%Y%m%d_%H%M%S); true'
scp -q -o ControlPath=/tmp/wq2508-%r@%h "$CONF" "$N":/tmp/mbpfan.conf
# Install without letting the package autostart on the distro default conf, then drop ours in.
$SSH 'printf "#!/bin/sh\nexit 101\n" | sudo tee /usr/sbin/policy-rc.d >/dev/null && sudo chmod 755 /usr/sbin/policy-rc.d; sudo NEEDRESTART_MODE=l DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::=--force-confold mbpfan; rc=$?; sudo rm -f /usr/sbin/policy-rc.d; exit $rc' >> "$EV" 2>&1 || { ev "FAIL apt install"; exit 1; }
$SSH 'sudo install -m644 /tmp/mbpfan.conf /etc/mbpfan.conf && sudo systemctl enable --now mbpfan && sleep 5 && systemctl is-active mbpfan' >> "$EV" 2>&1
ev "installed: $($SSH 'dpkg-query -W mbpfan; systemctl is-active mbpfan; systemctl is-enabled mbpfan' | paste -sd" ")"
sleep 5
a=$($SSH 'd=/sys/devices/platform/applesmc.768; echo $(cat $d/fan1_manual) $(cat $d/fan1_output) $(cat $d/fan1_input)')
ev "ASSERT fan1_manual fan1_output fan1_input = $a"
[ "${a%% *}" = 1 ] || { ev "FAIL: fan1_manual!=1 — mbpfan green but not driving the fan"; }

hold idle_mbpfan 300
load load_mbpfan 1800
hold cool_mbpfan 600
ev "DONE"
