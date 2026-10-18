#!/usr/bin/env bash
# wq#4206 root step: load the userns AppArmor profile for the pinned Chrome binary.
#   lumen-run start --root --name wq4206-apparmor -- <this>
# install.sh --verify checks the result (profile file + a sandboxed Chrome start).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
PROFILE=/etc/apparmor.d/lumen-stealth-browser-chrome

install -m 0644 -o root -g root "$SRC/lumen-stealth-browser-chrome.apparmor" "$PROFILE"
apparmor_parser -r "$PROFILE"
echo "loaded $PROFILE"
