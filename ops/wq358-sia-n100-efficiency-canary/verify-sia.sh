#!/usr/bin/env bash
# wq#358 — read-only status snapshot for the n100-efficiency (wq#357) pack on Sia.
# Mirrors ops/wq415-sobek-n100-efficiency-canary/verify-sobek.sh. No privilege required
# on the target for any of these reads. Safe to run repeatedly through the 24-48h soak
# window and to diff against baseline-sia-pre.md.
#
# Sia specifics (per wq#358 ticket): RTL8125 2.5G PCIe NIC on enp1s0 (LAN static .174) —
# Sia is THE canary for the ASPM=powersave decision, so this also checks NIC link state
# and error counters. Miniflux + news_intake + PG17 are containerized here (unlike Bes/
# Sobek's systemd postgresql), so those checks go through docker instead of systemctl.
#
# Usage: bash verify-sia.sh
set -euo pipefail
HOST=sia
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=8 $HOST"

echo "=== wq#358 sia n100-efficiency verify — $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

$SSH '
R=/sys/class/powercap/intel-rapl:0
echo "PL1_uW=$(cat $R/constraint_0_power_limit_uw 2>/dev/null)"
echo "PL2_uW=$(cat $R/constraint_1_power_limit_uw 2>/dev/null)"
echo "EPP=$(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null)"
echo "swappiness=$(cat /proc/sys/vm/swappiness)"
echo "unit_active=$(systemctl is-active n100-efficiency.service 2>/dev/null)"
echo "unit_enabled=$(systemctl is-enabled n100-efficiency.service 2>/dev/null)"
echo "thermald_active=$(systemctl is-active thermald 2>/dev/null)"
# RTL8125 on enp1s0 — the ASPM canary link. Watch operstate + drop/error counters.
echo "nic_operstate=$(cat /sys/class/net/enp1s0/operstate 2>/dev/null)"
echo "nic_carrier_changes=$(cat /sys/class/net/enp1s0/carrier_changes 2>/dev/null)"
echo "nic_rx_errors=$(cat /sys/class/net/enp1s0/statistics/rx_errors 2>/dev/null)"
echo "nic_tx_errors=$(cat /sys/class/net/enp1s0/statistics/tx_errors 2>/dev/null)"
echo "nic_rx_dropped=$(cat /sys/class/net/enp1s0/statistics/rx_dropped 2>/dev/null)"
NIC_PCI=$(basename "$(readlink -f /sys/class/net/enp1s0/device 2>/dev/null)" 2>/dev/null)
echo "nic_pci_addr=$NIC_PCI"
echo "nic_pci_power_control=$(cat /sys/bus/pci/devices/$NIC_PCI/power/control 2>/dev/null)"
# Containerized services (PG17, miniflux, news_intake) — docker health/state, not systemctl.
echo "--- docker ps (name/status) ---"
docker ps --format "{{.Names}}\t{{.Status}}" 2>/dev/null || echo "docker_ps_failed"
'

echo "=== end verify ==="
