# Boot arguments

The working configuration adds one load-bearing kernel command-line
override on top of whatever `armbianEnv.txt`/`uEnv.txt` ships by default in
the ophub `s905x3` build:

```
mem=3552M
```

## Why

Per the bring-up (wq#4318), the default memory map handed to the kernel on
this board does not leave a clean, usable region once the vendor
BL2/secure-monitor reservations and U-Boot's own carve-outs are accounted
for — `mem=3552M` is the cap that produced a stable boot on real hardware.
See `DEVICE_QUIRKS.md` → "Memory" for the DTB-side `reserved-memory` layout
(secmon + 256 MiB CMA) this number has to fit around.

This repo does not yet have the full before/after kernel log that would
pin down the exact failure mode without the cap (crash signature, which
region collided) — that evidence lives with the original RE session and is
one of the items the follow-up pull (`README.md` → "Status") is meant to
bring across. Until then, treat `mem=3552M` as a verified-working value
carried over from the bring-up, not as independently re-derived here.

## Where it goes

Same place any ophub `s905x3` boot-arg override goes: the `APPEND=` (or
equivalent) line in the image's `armbianEnv.txt` on the boot partition, or
appended to `bootargs` if you're hand-editing `uEnv.txt`/`boot.scr`
directly. `FLASH_PROCEDURE.md` and `provision.sh` show where this sits
relative to the DTB/overlay changes.
