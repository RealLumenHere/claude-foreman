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

Use **Balena Etcher** to write the image to a USB stick or SD card — not
`dd`. This isn't a cosmetic preference: the fleet's own flashing rule for
this project is Etcher-only for USB/SD writes (verified image integrity +
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

Note: `provision.sh` as shipped here expects
`meson-sm1-spectrio-tf1.dtb` to sit next to it. The binary `.dtb` itself
isn't in this repo yet — see `README.md` → "Status". Until it's pulled
across, build it from the `.dts` excerpt and base-board source noted in
`DEVICE_QUIRKS.md`, or substitute your own board DTB with the same
Ethernet-node fix applied.

## 4. First boot (USB/SD trial)

Per the G12A/G12B/SM1 family's BootROM limitation (see
`DEVICE_QUIRKS.md`), this SoC's low-level USB-boot protocol
(`boot-g12.py` in `pyamlboot`) can only push a `u-boot.bin` — it cannot
chainload straight to a Linux+rootfs image the way the GXL-family
`boot.py ums-emmc` path can. In practice this means: boot the
already-overlaid USB/SD media the normal way the vendor bootloader
supports for this board (its own boot-mode selection — e.g. holding the
mode/recovery button while powering on, or the equivalent the vendor
bootloader's `uEnv.txt`/boot-select logic already honors), rather than
relying on `pyamlboot` to push a full boot chain over USB from a dead
BootROM state. Confirm you reach a serial or network login before moving
to a permanent install.

## 5. Permanent install

Once the overlaid image boots cleanly from USB/SD, follow
`EMMC_INSTALL.md` to move it to the internal eMMC.
