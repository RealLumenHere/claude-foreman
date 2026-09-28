#!/usr/bin/env bash
# Sideload SepAlarm to Watch8. Run ON Thor (adb + mdns live there):
#   scp the APK to Thor first, then: bash sideload_thor.sh <apk-path>
# NOTE for Lumen workers: do NOT run this yourself over ssh mid-lane; it needs the
# watch awake + wireless debugging on, which is a Frank-adjacent physical state.
set -euo pipefail

ADB=/opt/platform-tools/adb
APK="${1:?usage: sideload_thor.sh <apk-path>}"

# Watch8 advertises _adb-tls-connect._tcp when wireless debugging is on.
"$ADB" mdns services
WATCH="$("$ADB" mdns services | awk '/_adb-tls-connect/{print $NF; exit}')"
test -n "$WATCH" || { echo "watch not advertising adb — enable wireless debugging on-wrist" >&2; exit 1; }

"$ADB" connect "$WATCH"
"$ADB" -s "$WATCH" install -r "$APK"

# Grant runtime perms so the FGS starts without on-wrist dialogs.
"$ADB" -s "$WATCH" shell pm grant industries.lumen.sepalarm android.permission.BLUETOOTH_CONNECT
"$ADB" -s "$WATCH" shell pm grant industries.lumen.sepalarm android.permission.POST_NOTIFICATIONS

# Kick the service once (normally MainActivity or BOOT_COMPLETED does this).
"$ADB" -s "$WATCH" shell am start -n industries.lumen.sepalarm/.MainActivity
echo "INSTALLED + STARTED"
