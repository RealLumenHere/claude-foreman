# SepAlarm (wq#1561)

Watch-only Wear OS app: audible separation alarm on STREAM_ALARM when the Watch8
loses the S26, zero lingering cards. Replaces FingerFind. Design + rationale:
`design/wq1561_watch8_separation_alarm.md` (repo root).

## Layout

- `app/src/main/java/industries/lumen/sepalarm/`
  - `SepAlarmService.kt` — FGS (type connectedDevice); ACL broadcasts accelerate,
    NodeClient poll (30 s) is the state of record
  - `AlarmPlayer.kt` — USAGE_ALARM tone + vibration, 10 s cycle, 40→100% escalation,
    reconnect chirp
  - `Notifications.kt` — the anti-card contract: one IMPORTANCE_MIN silent FGS note,
    nothing per-event
  - `MainActivity.kt` — status + DISMISS + permission bootstrap
  - `StatusTileService.kt` — glanceable CONNECTED/SEPARATED tile
  - `BootReceiver.kt` — restart FGS after reboot

## Ship (op lane, not a worker lane — needs gradle/adb)

```
# build (on Lumen)
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/spikes/wq1561_watch8_sepalarm/scripts/build.sh'
# sideload (on Thor, watch wireless debugging on)
bash scripts/sideload_thor.sh app-debug.apk
# after a week proven: disable FingerFind (revert: --revert)
bash scripts/disable_fingerfind.sh
```

Interim Samsung `disconnection_alert_on=0` (2026-09-08) stays 0 — SepAlarm replaces
that source too. Its revert script if ever needed:
`thor:~/watch8_battery/session_20260908/REVERT_402_20260908.sh`.
