# Boot arguments

The working configuration adds one load-bearing kernel command-line
override on top of whatever `armbianEnv.txt`/`uEnv.txt` ships by default in
the ophub `s905x3` build:

```
mem=3552M
```

The working image also carries `earlycon=meson,0xff803000`. It isn't
needed to boot, but it gets you serial output from the first kernel
instruction (UART_AO at 0xff803000, 115200 8N1), which is how a
`mem=`-less panic shows up at all.

## Why

The vendor U-Boot on this board (2015 era, prompt `sm1_ac214_v1#`) never
tells the kernel about the TrustZone/BL31 memory at the top of DRAM. The
board DTB has nothing there either: its only `reserved-memory` entries are
the two low `secmon` regions, and its `/memory@0` is a `0xffffffff`
placeholder (see `DEVICE_QUIRKS.md` → "Memory"). Without a cap, the kernel
maps the secure region as ordinary RAM and dies in `paging_init` with an
**Asynchronous SError Interrupt**. `mem=3552M` (0xDE000000) keeps the kernel
below that region. It costs the top ~544 MiB, leaving ~3.4 GB usable.

How this was established: the fleet's bring-up notes record the root cause
in exactly those terms, the fleet's provisioning checks refuse any boot
media whose uEnv.txt lacks `mem=3552M` ("kernel SError panic risk"), and
the working community image has the arg in its uEnv.txt. The DTB facts
above were re-checked against the shipped blob for this writeup. No raw
serial capture of the panic was kept, so this repo can't quote the panic
log line for line.

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

There is no transcript of this cap being applied and surviving a boot on
the real board. wq#4326 searched the fleet's bring-up tree and found none
(see `README.md` → "Status"). The working community image ships with the
cap in its `uEnv.txt`, and that image is the evidence that the cap boots.
