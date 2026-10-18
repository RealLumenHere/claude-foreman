#!/usr/bin/env bash
# wq#4181 — shared Armbian (ophub) hardening pack for TF1 S905X3 appliances.
# Apply before any role-specific config (see roles.yaml: hardening_script).
# Idempotent: re-running after a reboot or re-apply is safe.
#
# Not run against real hardware by this ticket — no fleet-exec grant for TF1
# units yet (same blocker ops/wq3430-spectrio-tf1-deploy/README.md recorded).
# Ships here so the eMMC install path (spectrio_zero_touch_and_emmc.md) has a
# fill-in-the-blanks script to run once a unit is on the bench.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "harden-armbian.sh must run as root" >&2
  exit 1
fi

ARMBIAN_ENV=/boot/armbianEnv.txt

# 1. Boot to a text console only — no desktop/display manager running on an
# appliance with no keyboard attached.
systemctl set-default multi-user.target

# 2. HDMI off: disable the HDMI-A-1 connector at the kernel cmdline (mainline
# connector syntax video=<connector>:d). ophub boots via u-boot + armbianEnv.txt,
# not grub, so the cmdline edit goes through extraargs there.
if [ -f "$ARMBIAN_ENV" ] && ! grep -q 'video=HDMI-A-1:d' "$ARMBIAN_ENV"; then
  if grep -q '^extraargs=' "$ARMBIAN_ENV"; then
    sed -i 's/^extraargs=.*/& video=HDMI-A-1:d/' "$ARMBIAN_ENV"
  else
    echo 'extraargs=video=HDMI-A-1:d' >> "$ARMBIAN_ENV"
  fi
fi

# 3. USB autosuspend — default every USB device, current and future, to
# power/control=auto via udev rather than a one-shot sysfs write that a
# hotplugged device would miss.
install -m 0644 /dev/stdin /etc/udev/rules.d/90-usb-autosuspend.rules <<'RULES'
ACTION=="add", SUBSYSTEM=="usb", TEST=="power/control", ATTR{power/control}="auto"
RULES

# 4. rfkill wifi off, persistently. rfkill alone does not survive reboot;
# systemd-rfkill restores saved block state from /var/lib/systemd/rfkill on boot.
rfkill block wifi 2>/dev/null || true
systemctl enable systemd-rfkill.service

# 5. ramlog + volatile journal — keep write-heavy logs off the eMMC.
systemctl enable armbian-ramlog.service 2>/dev/null || true
mkdir -p /etc/systemd/journald.conf.d
install -m 0644 /dev/stdin /etc/systemd/journald.conf.d/99-volatile.conf <<'JRNL'
[Journal]
Storage=volatile
RuntimeMaxUse=32M
JRNL

# 6. noatime + commit=600 on every ext4 mount in fstab — fewer eMMC writes.
if [ -f /etc/fstab ]; then
  sed -i -E 's/^([^#].*[[:space:]]ext4[[:space:]]+)([^[:space:]]+)([[:space:]]+[0-9]+[[:space:]]+[0-9]+)$/\1\2,noatime,commit=600\3/' /etc/fstab
fi

# 7. meson hardware watchdog, 30s timeout, systemd-managed (Amlogic S905X3's
# watchdog driver is meson_gxbb_wdt; systemd pets /dev/watchdog itself once
# RuntimeWatchdogSec is set, no separate watchdog daemon needed).
mkdir -p /etc/systemd/system.conf.d
install -m 0644 /dev/stdin /etc/systemd/system.conf.d/99-watchdog.conf <<'WDT'
[Manager]
RuntimeWatchdogSec=30
WDT
echo meson_gxbb_wdt > /etc/modules-load.d/meson-wdt.conf

echo "harden-armbian.sh: applied. Reboot for HDMI/journald/watchdog changes to take effect."
