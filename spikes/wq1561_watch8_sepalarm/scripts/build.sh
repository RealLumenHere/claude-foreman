#!/usr/bin/env bash
# Build SepAlarm on Lumen. Run ON Lumen (from another box:
#   ssh lumen@100.117.66.102 'bash /path/to/spikes/wq1561_watch8_sepalarm/scripts/build.sh')
# Needs: /home/lumen/android-sdk (present), JDK 17, gradle (or the wrapper once generated).
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
export ANDROID_HOME="${ANDROID_HOME:-/home/lumen/android-sdk}"

if [ -x "$PROJ/gradlew" ]; then
    GRADLE="$PROJ/gradlew"
elif command -v gradle >/dev/null; then
    GRADLE=gradle
    # First run: generate the wrapper so later builds are hermetic.
    (cd "$PROJ" && gradle wrapper --gradle-version 8.9)
else
    echo "no gradle found — install gradle or commit a wrapper" >&2
    exit 1
fi

(cd "$PROJ" && "$GRADLE" :app:assembleDebug)
APK="$PROJ/app/build/outputs/apk/debug/app-debug.apk"
test -f "$APK" && echo "BUILT: $APK"
