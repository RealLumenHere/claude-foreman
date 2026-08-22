#!/usr/bin/env bash
# wq#415 — read-only status snapshot for the n100-efficiency (wq#357) canary on Sobek.
# No privilege required on the target (Sobek's `sobek` user is in `adm`, which covers
# journalctl). Safe to run repeatedly through the 24-48h soak window and to diff
# against baseline-sobek-pre.md.
#
# Usage: bash verify-sobek.sh
set -euo pipefail
HOST=sobek@100.86.156.91
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o ConnectTimeout=8 $HOST"

echo "=== wq#415 sobek n100-efficiency verify — $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

$SSH '
R=/sys/class/powercap/intel-rapl:0
echo "PL1_uW=$(cat $R/constraint_0_power_limit_uw 2>/dev/null)"
echo "PL2_uW=$(cat $R/constraint_1_power_limit_uw 2>/dev/null)"
echo "EPP=$(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null)"
echo "swappiness=$(cat /proc/sys/vm/swappiness)"
echo "unit_active=$(systemctl is-active n100-efficiency.service 2>/dev/null)"
echo "unit_enabled=$(systemctl is-enabled n100-efficiency.service 2>/dev/null)"
# Protected PCI classes must stay "on" (net/SATA/NVMe excluded from runtime PM by design)
echo "pci_sata_00:17.0=$(cat /sys/bus/pci/devices/0000:00:17.0/power/control 2>/dev/null)"
echo "pci_nic_01:00.0=$(cat /sys/bus/pci/devices/0000:01:00.0/power/control 2>/dev/null)"
echo "pci_nvme_02:00.0=$(cat /sys/bus/pci/devices/0000:02:00.0/power/control 2>/dev/null)"
echo "thermald_active=$(systemctl is-active thermald 2>/dev/null)"
echo "pg_active=$(systemctl is-active postgresql 2>/dev/null)"
echo "pg_isready=$(pg_isready 2>&1)"
echo "walreceiver=$(ps -eo cmd | grep -c "walreceiver streaming")"
ps -eo cmd | grep "walreceiver streaming" | grep -v grep || echo "walreceiver_line=NONE"
echo "pg_journal_errors_last200=$(journalctl -u postgresql --no-pager -n 200 -o cat | grep -icE "fatal|panic|error")"
'
