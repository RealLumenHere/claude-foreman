#!/usr/bin/env bash
# Ship step: disable FingerFind once SepAlarm is proven. Run ON Thor.
#   bash disable_fingerfind.sh            -> disable
#   bash disable_fingerfind.sh --revert   -> re-enable (revert path)
set -euo pipefail

ADB=/opt/platform-tools/adb
PKG=com.finger.find.app
WATCH="$("$ADB" mdns services | awk '/_adb-tls-connect/{print $NF; exit}')"
test -n "$WATCH" || { echo "watch not reachable over adb" >&2; exit 1; }
"$ADB" connect "$WATCH" >/dev/null

if [ "${1:-}" = "--revert" ]; then
    "$ADB" -s "$WATCH" shell pm enable "$PKG"
    echo "REVERTED: $PKG re-enabled"
else
    "$ADB" -s "$WATCH" shell pm disable-user --user 0 "$PKG"
    echo "DISABLED: $PKG (revert: $0 --revert)"
fi
"$ADB" -s "$WATCH" shell pm list packages -d | grep -F "$PKG" || true
