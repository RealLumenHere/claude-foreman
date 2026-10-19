#!/bin/bash
# provision.sh — apply the Spectrio overlay to a freshly-flashed ophub Armbian s905x3 stick.
# Usage (on the flashing host, with the stick's BOOT partition mounted):
#   ./provision.sh /run/media/<user>/BOOT
# Base image: Armbian_26.11.0_amlogic_s905x3_resolute_6.18.51_server (or any ophub s905x3 build).
#
# Reproduced from the fleet's working copy (projects/spectrio-tf1/golden/provision.sh)
# as part of publishing this bring-up; see DEVICE_QUIRKS.md for why each of these three
# changes is needed.
set -e
M="$1"; [ -d "$M/dtb/amlogic" ] || { echo "usage: $0 <BOOT mountpoint>  (must be the ophub BOOT FAT)"; exit 1; }
D="$(cd "$(dirname "$0")" && pwd)"
cp "$D/meson-sm1-spectrio-tf1.dtb" "$M/dtb/amlogic/meson-sm1-spectrio-tf1.dtb"      # PEF7071 @ MDIO 5, rgmii, no rx-delay
cp "$M/u-boot-x96maxplus.bin" "$M/u-boot.ext"                                         # chainload target the vendor u-boot needs
sed -i 's|^FDT=.*|FDT=/dtb/amlogic/meson-sm1-spectrio-tf1.dtb|' "$M/uEnv.txt"
sync
echo "overlay applied:"; grep '^FDT' "$M/uEnv.txt"; ls -l "$M/u-boot.ext" "$M/dtb/amlogic/meson-sm1-spectrio-tf1.dtb"
