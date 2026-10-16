# wq#4072 — Miko 3 deep partition & peripheral verification toolkit

Pre-flash verification toolkit for MIKO1070XXM3E0408B7, built under a zero-tolerance
posture: three Miko 3 units are already bricked, so nothing in this directory writes to
the device. Every script is read-only probe/backup/capture; the write-guard in
`partition_backup.guard_command()` enforces that even for future edits.

Full writeup (procedure order, VID:PID identification, cross-reference against
benwilson/miko3-jailbreak's MT8168 hardware docs, open items): see
`scratch/research/miko3-partition-safety-map.md`.

## Built here (BUILT+STAGED — not run against hardware in this sandbox)

Same posture as wq#349's UART PoC: this sandbox has no execution path onto the bench
host where the Miko 3 is wired (no ssh/adb/fastboot in `harness/worker-settings.json`'s
allowlist — see ISSUE-LOG #114). Everything below is authored + unit-tested on Lumen
with zero hardware attached; `uart_identify.py`'s and `partition_backup.py`'s pure
logic has `pytest` coverage under `tests/` (19 tests, all passing). The actual run
against the unit is a bench-host, hands-on-device task.

| Ticket step | Script | Hardware-dependent? |
|---|---|---|
| 1. Full partition backup + SHA256 manifest | `partition_backup.py` | yes (adb root required) |
| 2. probe-stock.sh for FB params | `probe_stock.py` | yes (fastboot) |
| 3. FACTFACT META test | not built — see safety map "Open items" | n/a |
| 4. i2cdetect all buses | `i2c_survey.sh` | yes |
| 5. /dev/ttyS* motor UART identification | `uart_identify.py` | yes (pyserial) |
| 6. full dmesg capture | `dmesg_capture.sh` | yes |
| 7. SD card slot test | `sdcard_slot_test.sh` | yes |

## Deploy (bench host, once the unit is wired)

```
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4072-miko3-partition-safety/i2c_survey.sh'
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4072-miko3-partition-safety/dmesg_capture.sh'
ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4072-miko3-partition-safety/uart_identify.py'
ssh lumen@100.117.66.102 'bash /home/lumen/foreman/ops/wq4072-miko3-partition-safety/sdcard_slot_test.sh /dev/sdX'
ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4072-miko3-partition-safety/probe_stock.py run'
ssh lumen@100.117.66.102 'python3 /home/lumen/foreman/ops/wq4072-miko3-partition-safety/partition_backup.py manifest <image-dir>'
```

`partition_backup.py backup-all` (the `backup_all_partitions()` function) needs the
device already in `adb root` shell per ticket step 1 — this toolkit does not elevate
privilege itself.

## Safety invariant

No `fastboot flash`/`erase`/`format`/`wipe`, no `dd of=/dev/*`, no `adb push` to a
device path appears anywhere in this directory. `partition_backup.guard_command()` is
the single choke point every subprocess call in this toolkit passes through first —
run `python3 partition_backup.py guard-check <argv...>` to test any new command before
wiring it in.
