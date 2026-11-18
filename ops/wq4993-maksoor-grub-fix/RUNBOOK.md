# wq#4993 Maksoor: GRUB fix after 512GB -> 1TB clone

Status: runbook only. Not executed by the build worker (no KVM/SSH to Maksoor in its sandbox; `prompts/maksoor-boot-fix.md` is outside its allowed dirs).

Known good: rsync clone complete; fstab root UUID `1a6c3215-375e-4909-98a1-172a74529d21`, EFI `97CC-2A60`.
Cause: `update-grub` ran in a chroot without `/dev /proc /sys` bound, so `grub.cfg` was never regenerated.

## 1. Manual boot from `grub>` (via KVM)

    search --no-floppy --fs-uuid --set=root 1a6c3215-375e-4909-98a1-172a74529d21
    ls ($root)/boot
    linux ($root)/boot/vmlinuz root=UUID=1a6c3215-375e-4909-98a1-172a74529d21 ro
    initrd ($root)/boot/initrd.img
    boot

If `/boot/vmlinuz` is absent, use the exact file names `ls` printed (tab-complete).

## 2. Repair from the booted system (SSH-wrapped from another node)

    ssh maksoor@100.81.204.74 'findmnt /boot/efi && lsblk -f'
    ssh maksoor@100.81.204.74 'sudo grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu --recheck'
    ssh maksoor@100.81.204.74 'sudo update-grub'
    ssh maksoor@100.81.204.74 'sudo efibootmgr -v'

Confirm `/boot/efi` is the partition with UUID `97CC-2A60` before running `grub-install`. Use the distro's real bootloader-id if it is not `ubuntu` (`ls /boot/efi/EFI`).

## 3. Verify

`grep -c 'root=UUID=1a6c3215' /boot/grub/grub.cfg` is non-zero. Reboot (Maksoor is not Thor) and confirm it reaches the OS without `grub>`.

If a chroot is ever needed again: bind `/dev /dev/pts /proc /sys /sys/firmware/efi/efivars` first.
