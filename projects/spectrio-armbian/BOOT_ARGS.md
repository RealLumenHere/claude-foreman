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

On ophub's Amlogic images the kernel command line lives in `uEnv.txt` on
the BOOT partition — not in `armbianEnv.txt`. `uEnv.txt` carries `LINUX=`,
`INITRD=`, `FDT=` and `APPEND=`, and `APPEND=` is the command line, so the
cap goes on the end of that one line:

```
APPEND=root=UUID=<rootfs-uuid> rootfstype=ext4 rootflags=data=writeback rw
       console=ttyAML0,115200n8 console=tty0 ... mem=3552M
```

(one physical line in the file). `armbianEnv.txt` + `extraargs=` is the
mainline-Armbian equivalent you would edit on a stock Armbian board; it is
not the path this image family uses for Amlogic boot args. Confirmed
against ophub's own boot-configuration documentation for
`amlogic-s9xxx-armbian`, which both documents `APPEND=` as "kernel
command-line parameters" in `uEnv.txt` and shows `armbian-install`
rewriting the `root=...console=ttyAML0` portion of that same line.

### Not yet demonstrated in this repo

Nothing shipped here applies the cap for you. `provision.sh` does edit
`uEnv.txt`, but only the `FDT=` line — `FLASH_PROCEDURE.md` step 3 lists
its three actions as the complete set of what it touches — so a reader who
follows the flash procedure end to end ends up with the DTB overlay applied
and **no memory cap**. Editing `APPEND=` is a manual step until that is
automated and re-verified on hardware.

The transcript showing this cap being applied and surviving a boot on the
real board lives with the original RE session, not in this repo. It is a
third pending item alongside the board `.dtb` binary and the before/after
kernel log above — see `README.md` → "Status".
