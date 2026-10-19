# eMMC install guide

Once the overlaid image (see `FLASH_PROCEDURE.md`) boots cleanly from
USB/SD, move it to the internal eMMC so the box no longer depends on
removable media.

## Procedure

Run ophub's own `armbian-install` from the booted USB/SD system, selecting:

- SoC: `s905x3`
- Board DTB: the overlaid `meson-sm1-spectrio-tf1.dtb` (not the stock
  ophub board choice)
- Bootloader profile: `x96maxplus` (matches the `u-boot-x96maxplus.bin`
  chainload target `provision.sh` already set up — see
  `FLASH_PROCEDURE.md` step 3)

`armbian-install` lays out the eMMC as:

| Partition | Offset | Contents |
|---|---|---|
| vendor bootloader + env | start of eMMC | **left intact** — not overwritten |
| `BOOT_EMMC` | @ 117 MiB | kernel, DTB, boot config |
| `ROOTFS_EMMC` | @ 629 MiB | root filesystem |

The vendor bootloader and its environment stay below the 117 MiB mark with
no overlap into `BOOT_EMMC` — both ends have been confirmed `fsck` clean on
this board.

## Why the boot chain still needs the vendor bootloader

Mainline U-Boot fails to initialize this board's eMMC controller (fails
with a `-110` voltage-select error during `mmc_power_up`/signal voltage
negotiation). That means a mainline-U-Boot-only eMMC boot path is **not**
viable on this hardware yet — the only boot chain proven to work end to
end is the one already in place: the vendor bootloader's own
`start_emmc_autoscript` → `booti` sequence, loading the Armbian
kernel/DTB from `BOOT_EMMC` the way `armbian-install`'s `x96maxplus`
profile sets it up. Don't try to replace the vendor bootloader with a
from-scratch mainline U-Boot build on the eMMC path — it won't bring the
eMMC up.

## Verifying

After the install and a reboot with removable media pulled:

- confirm the box boots to login without the USB stick/SD card inserted;
- confirm `ip addr show eth0` comes up (this is the real end-to-end proof
  that the PEF7071 Ethernet fix in `DEVICE_QUIRKS.md` survived the
  eMMC install, not just the USB/SD trial boot);
- `fsck` both `BOOT_EMMC` and `ROOTFS_EMMC` clean before considering the
  install final.
