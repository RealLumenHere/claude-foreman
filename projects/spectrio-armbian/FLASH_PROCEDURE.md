# Flash procedure

## 1. Get a base image

Download an `s905x3` build from
[ophub/amlogic-s9xxx-armbian](https://github.com/ophub/amlogic-s9xxx-armbian)
(releases page). The working base used for this bring-up was
`Armbian_26.11.0_amlogic_s905x3_resolute_6.18.51_server` — any current
`s905x3` build from the same project should work, but that exact
build/kernel combination is the one this board's quirks have been verified
against.

## 2. Write it to removable media

Use **Balena Etcher** to write the image to a **USB stick**, not with `dd`.
Don't use an SD card: this box's vendor U-Boot maps `recovery_from_sdcard`
to `fatload mmc 0`, which reads the eMMC, not the SD slot, so an SD card
never boots. Run `fsck.fat -a` on partition 1 and `fsck.ext4 -n` on
partition 2 after writing. This isn't a cosmetic preference: the fleet's own flashing rule for
this project is Etcher-only for USB writes (verified image integrity +
progress/verify pass that a bare `dd` doesn't give you), and that's the
tool this bring-up was actually verified with.

## 3. Apply the board overlay

Mount the freshly-written image's `BOOT` partition and run `provision.sh`
(shipped in this directory) against the mountpoint:

```
./provision.sh /run/media/<user>/BOOT
```

This does three things (see the script; it's short and this is the
complete list of what it touches):

1. Copies `meson-sm1-spectrio-tf1.dtb` into `BOOT/dtb/amlogic/`.
2. Copies the vendor-provided `u-boot-x96maxplus.bin` already on the BOOT
   partition to `u-boot.ext` — this is the chainload target ophub's own
   boot script looks for.
3. Points `FDT=` in `uEnv.txt` at the copied-in DTB.

`provision.sh` expects `meson-sm1-spectrio-tf1.dtb` in the same directory.
It ships here, along with its decompiled `meson-sm1-spectrio-tf1.dts`.

**`provision.sh` does not add the boot args.** On its own, the overlay
produces a stick that panics in `paging_init`. After running it, edit
`BOOT/uEnv.txt` and append `mem=3552M earlycon=meson,0xff803000` to the
`APPEND=` line (see `BOOT_ARGS.md` for why). The script is kept exactly as
it ran on the fleet, so this step stays manual.

## 4. First boot (USB trial)

1. Plug the stick into the box's **blue USB-A port**. That's the host port,
   not the OTG port.
2. Power on while holding the reset button behind the AV jack with a
   toothpick for 5 seconds.
3. The vendor U-Boot loads `aml_autoscript` from the stick, saves the boot
   path with `saveenv`, and chainloads `u-boot.ext`, which boots the kernel.
4. Later boots don't need the toothpick.
5. Log in as `root` / `1234`. Armbian's first-run wizard asks for a new
   password.

This is the only practical first-boot path. `pyamlboot`'s `boot-g12.py`
(the USB-boot tool for the G12A/G12B/SM1 family, see `DEVICE_QUIRKS.md`)
can only push a `u-boot.bin`, not a whole Linux+rootfs boot chain.
Serial console: the 4-pin header near the IR receiver, pinned
GND/TX/RX/VCC (leave VCC unconnected), 115200 8N1. The vendor U-Boot prompt
is `sm1_ac214_v1#`, and you get a 1-second window at power-on to break into
it. Confirm a serial or network login, and that `eth0` links at 1000 Mb/s,
before installing permanently.

## 5. Permanent install

Once the overlaid image boots cleanly from USB, follow `EMMC_INSTALL.md`
to move it to the internal eMMC.

## Recovery

If a bad write bricks the bootloader, the box still enters Amlogic USB
burn mode on the OTG port (USB `1b8e:c003`). From there, `pyamlboot` can
restore the vendor bootloader from a backup. Back up every unit's eMMC
partitions before writing anything.
