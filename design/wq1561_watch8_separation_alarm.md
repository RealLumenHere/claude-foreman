# wq#1561 — Watch8 native separation alarm (replaces FingerFind)

Frank need (2026-09-08): audible alert when the watch and the S26 separate. On-screen
cards are useless — he clears them constantly. Root cause is the *app class*, not
config: FingerFind (com.finger.find.app 1.0.4) is notification-driven end to end:

- channel `connectStatusNotification` → plays the *system notification tone*
  (silent in vibrate mode, exactly when Frank needs sound),
- persistent FGS card "Checking connection status." that never goes away,
- one card per disconnect AND one per reconnect,
- the "Sound Alarm" toggle lives only in app prefs (`soundDisconnectAlarm` /
  `soundFarAlarm`) with no adb-settable path.

No config knob fixes that. Replacement app: `SepAlarm`, watch-only, source at
`spikes/wq1561_watch8_sepalarm/`.

## Design

**Detection — two sources, reconciled.**
1. Primary (event, instant): `BluetoothAdapter.ACTION_ACL_DISCONNECTED` /
   `ACTION_ACL_CONNECTED` broadcasts. Fires the moment the phone link drops; no
   polling cost.
2. Truth (poll, slow): `Wearable.NodeClient.connectedNodes` every 30 s from the FGS.
   ACL broadcasts can be missed across process death, and the companion proxy can ride
   non-BT transports (Wi-Fi/LTE bridging); the node poll corrects both directions —
   it cancels a stale alarm if the phone is actually reachable, and raises one if the
   ACL event was lost. NodeClient is the state of record; ACL events only *accelerate*
   state changes.

**Alert — alarm stream, not notification tone.** On confirmed separation:
`MediaPlayer` with `AudioAttributes(USAGE_ALARM)` (audible in vibrate mode by design —
this is the whole point vs FingerFind's notification tone) + `VibrationEffect`
waveform. Repeats on a 10 s cycle, escalating volume 40%→100% over the first 3 cycles,
until reconnect or on-wrist dismiss. On reconnect: single short tone, no card.

**Zero lingering cards.**
- FGS notification (mandatory for a long-lived service) rides a channel created at
  `IMPORTANCE_MIN` with `setShowBadge(false)` and `setSilent(true)` — it exists only
  in the notification drawer's minimized section, never as a card, never a sound.
- Disconnect/reconnect events produce **no notifications at all** — sound + vibration
  + the app's own on-screen state carry the signal. Anything transient we ever add
  must use `setTimeoutAfter(8_000)` (constant enforced in `Notifications.kt`).
- FGS type `connectedDevice` (API 34 requirement, matches the actual use).

**On-wrist UI.** `MainActivity`: live status (CONNECTED / SEPARATED + since-when),
DISMISS button (silences the active alarm, monitoring continues), toggle for the
optional RSSI far-alert. `StatusTileService` gives a glanceable tile (tap → app).

**RSSI far-alert (optional, default off).** Poll bonded-phone RSSI via
`BluetoothGatt.readRemoteRssi` when connected; alert below threshold. Shipped behind
the toggle, default off — separation alarm is the need; far-alert was FingerFind's
gimmick.

**Boot + resilience.** `BOOT_COMPLETED` receiver restarts the FGS. `START_STICKY`.
Poll alarm via `AlarmManager.setExactAndAllowWhileIdle` fallback if the FGS coroutine
is throttled by doze.

## Ship path

1. Build on Lumen: `scripts/build.sh` (Android SDK at `/home/lumen/android-sdk`,
   gradle wrapper; not runnable from a worker lane — no gradle/java on the worker
   allowlist, so build is a follow-up op-lane step).
2. Sideload via Thor: `scripts/sideload_thor.sh` (mdns discovery,
   `/opt/platform-tools/adb`).
3. Disable FingerFind: `scripts/disable_fingerfind.sh`
   (`pm disable-user --user 0 com.finger.find.app`); revert path in the same script
   (`--revert` → `pm enable`).
4. Samsung interim dup-source (`disconnection_alert_on=0`, applied 2026-09-08) stays
   as-is until SepAlarm is proven for a week, then revert via
   `thor:~/watch8_battery/session_20260908/REVERT_402_20260908.sh` so Samsung's alert
   doesn't double-fire alongside ours — no, keep it 0: SepAlarm replaces it too.

## Why not fix FingerFind

Closed source, notification-driven architecture (the defect *is* the architecture),
prefs unreachable via adb. Patching the APK (smali edit of the channel importance)
was considered and rejected: still leaves per-event cards, still the persistent card,
and re-signing breaks updates.
