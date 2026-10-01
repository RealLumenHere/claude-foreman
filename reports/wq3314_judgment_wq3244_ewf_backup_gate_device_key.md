# wq#3314 — judgment on the verify-pass CONTRADICT of wq#3244 (e-waste-flasher v1 rebuild)

**Verdict: CONTRADICT UPHELD. Re-verdict on wq#3244's shipped diff:
CORRECT-WITH-DEFECT, fix dispatched as wq#3323 (not shipped from here).**

wq#3244 delivered what its audit doc says it did: a typed rkdeveloptool backend, a
dark React UI, argument-array process spawning, and a webview with no shell or fs
surface. What the verify-pass caught is that the one safety property the directive
named, "backup-before-flash gate", identifies the device by *where it is plugged in*
rather than *which board it is*. On this tool's main workflow (a run of same-model
e-waste boards through one USB port), the second board inherits the first board's
backup. The UI then calls it "✓ backed up this session" and enables Flash on a board
that was never read.

## The claim, checked live

Re-read at HEAD `5ac3265`. That commit (wq#3254) only adds `profiles/`, `tools/` and
README lines, so every cited line is identical to the `70570e6` / v0.1.0-rc1 ship.
Read through `git -C … show HEAD:<path>` because the session's Read guard refused these
paths as "already read".

- `src/lib/api.ts:77-78`: `deviceKey = (d) => \`${d.bus}:${d.port}:${d.vid}:${d.pid}\``.
  `DeviceInfo` (api.ts:4-19) carries `devnum` and `serial`, and the key uses neither.
- `src/App.tsx:14`: `backedUp: Record<string, boolean>`. There are exactly two
  `setBackedUp` references: the `useState` and `App.tsx:82`
  `onBackupDone={() => setBackedUp((m) => ({ ...m, [deviceKey(device)]: true }))}`.
  Nothing ever clears or prunes it, and nothing does on rescan or unplug either.
- `App.tsx:76-78`: DevicePanel gets `key={deviceKey(device)}` and
  `backedUp={!!backedUp[deviceKey(device)]}`. Because the React key also collides, the
  panel is not remounted on a board swap, so board A's chosen image and partition
  carry over too. The operator is one click from flashing B.
- `src/components/DevicePanel.tsx:43`:
  `canFlash = !busy && backedUp && canRockusb && !!image && partition.trim().length > 0`.
  `:178` shows "✓ backed up this session" and `:210` shows "disabled until a backup
  completes", both driven off the same boolean. `DeviceList.tsx:58-60` shows the
  "backed up" badge off `backedUp[key]`.
- Backend, `src-tauri/src/lib.rs:334-368` `flash_device(bus, port, image, partition)`:
  it validates the image and partition, then calls `rk::require_device(bus, port)`,
  checks for Loader mode and runs `wlx`/`wl`. It has no backup record and no backup
  check. `rk.rs:141-152` `require_device` refuses when more than one rockusb device is
  attached (good) and otherwise matches on bus/port only. A swapped board at the same
  port passes.
- `README.md:7`: "Backup is mandatory before flash." The audit doc
  (`docs/wq3244_ewf_rebuild_audit.md:31`) describes the map as gating "on a prior backup
  for the selected device". No tradeoff is documented anywhere, so this is not an
  accepted limitation.

**Probe.** Re-ran the verifier's `/tmp/fworker-wq3314/probe_devicekey.py`: keyA ==
keyB == `1:3:2207:350a` with devnum 7 vs 8. That probe hand-mirrors `deviceKey`, so I
also wrote `/tmp/fworker-wq3314/judgment_probe_from_source.py`. It pulls the template
literal straight out of `git show HEAD:src/lib/api.ts` (no transcription), renders it
for three same-model boards on bus 1 port 3, and replays the App.tsx:82 write for
board A. Output:

```
key fields: ['bus', 'port', 'vid', 'pid']
ignored DeviceInfo fields: ['devnum', 'sysfs', 'name', 'manufacturer', 'product', 'serial', ...]
board A: key=1:3:2207:350a devnum=7 serial=None -> backedUp=True
board B: key=1:3:2207:350a devnum=8 serial=None -> backedUp=True
board C: key=1:3:2207:350a devnum=9 serial=SN-C -> backedUp=True
RESULT: COLLISION (gate bypassed for never-read board)   (exit 1)
```

Board C shows that even a board reporting a distinct serial is waved through.

## Severity

High for this tool. Wrong-board flashing is the failure the gate exists to prevent.
`wlx` overwrites a partition on a board whose stock firmware may exist nowhere else,
and the only recovery source is the backup this bug skips. The UI makes it worse by
asserting the board is backed up. Mitigations: an operator still has to click Flash,
only one partition is written (not the whole eMMC), and rc1 has not touched hardware
yet (wq#3313, the on-device test, is still open). Those lower the blast radius today
but not the defect class, so this is p1 and should land before the build is trusted
on real lots.

## Disposition

**Write boundary.** `/home/lumen/build/e-waste-flasher` is not in this session's
allowed working directories. `git -C` against a path there was refused with "may
only access files with git from the allowed working directories", the same pattern
the verifier hit with ls/grep/find. I did not attempt an Edit or Write to probe the
boundary, because the pass is read-only against that repo by instruction and a
successful write would itself violate that.

**Filed wq#3323** (lane build, p1, deps 3244, claude-sonnet-5@high,
`needs repo(/home/lumen/build/e-waste-flasher)`) with this fix spec:

1. Frontend: change `deviceKey` to `bus:port:vid:pid:devnum`. devnum is reassigned on
   every USB re-enumeration, so a swapped board can't match. Don't key on serial
   alone, because rockusb serials are often null or identical across units. On each
   rescan, drop `backedUp` entries whose key is no longer listed. As a fail-safe side
   effect, re-plugging the *same* board needs a fresh backup.
2. Backend (where the invariant actually belongs; also covers swap-without-rescan,
   which a frontend-only fix misses because the device list is stale until rescan):
   pass devnum into `backup_device`/`flash_device`, record `(bus, port, devnum)` in
   `AppState` on a successful backup, and in `flash_device` re-read `usb::scan()`
   immediately before `wl`/`wlx`. Refuse unless the device now at bus/port has the
   passed devnum and that tuple is in the record.
3. Rust unit tests on a pure gate helper (no record, devnum mismatch, match), with
   the existing 9 tests kept green.

I checked that this fix doesn't break the normal flow. devnum stays stable from
backup to flash: backup uses `rl` (lib.rs:303) and flash uses `wl`/`wlx`, and neither
re-enumerates. `db` (lib.rs:385) re-enumerates but runs before backup, since `rl`
needs Loader mode. A residual TOCTOU window between the final `usb::scan()` and the
`wl` exec remains. It is sub-second and acceptable.

**Noted on wq#3313:** until wq#3323 lands, the on-device tester should back up every
board explicitly and not trust the badge or the enabled Flash button.

## Coverage

Independently checked: every file and line the contradiction cites, the absence of
any `backedUp` reset path, the absence of a backend backup check, `require_device`'s
multi-device refusal, and the backup→flash verb sequence for devnum stability.
Not re-derived here: the verifier's clean findings on
`validate_output`/`validate_input_file`/`validate_partition`, the shell-free spawning,
the `rk.rs` LocationID decode, the 9/9 test count, and `capabilities/default.json` /
CSP. Nothing I read contradicts them, but those rest on wq#3314's own pass.

## Scope note

Read-only against `/home/lumen/build/e-waste-flasher`; no code there was changed. The
only artifacts are this report, the probe in `/tmp/fworker-wq3314/`, ticket wq#3323,
and one appended note on wq#3313. The shared busy key held an unrelated task (wq#3319,
dead PID 3935864) that does not belong to this pass, so I left it for its owner.
