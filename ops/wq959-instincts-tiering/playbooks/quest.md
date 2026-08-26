# PLAYBOOK: quest (v1, 2026-08-26, wq#959)

Domain rules moved verbatim from `instincts.yaml` (tier-2 restructure, R1 §6). Trigger:
any Quest / QuestFree / quest-hardware / Android-ADB / phone-network work. The tier-2
index entry `quest-domain` in instincts.yaml points here.

## Quest firmware — source of truth
(added ≤2026-08-13)

QUEST FIRMWARE SOURCE OF TRUTH: for anything about Quest 1 monterey firmware behavior,
ALWAYS read from /home/lumen/re/quest-firmware/ (mounted system, decompile trees,
extracted ramdisk, docs/firmware/*.md) BEFORE web-searching. Web results are usually
about a different Quest model or a different firmware version and will mislead. The
disk has the exact firmware. Search episodes with `lumen_episode.py search '<term>'`
for prior findings.

> A7 C10 flags `search` → `recall` as the preferred episode verb; text kept verbatim
> pending that reword.

## QuestFree — project plan is law
(added ≤2026-08-13)

QUESTFREE PROJECT PLAN: at session start on any questfree/quest-hardware work, read
/home/lumen/questfree/docs/sop/PROJECT_PLAN.md and follow its rules verbatim. For any
device operation, follow /home/lumen/questfree/docs/sop/QUEST_TOUCH_CHECKLIST.md
phase-by-phase. Do not improvise around them.

## Q1 USB update-mode menu — never ask Frank again
(added ≤2026-08-13)

Q1 USB UPDATE MODE MENU: the bootloader menu on Q1 monterey has EXACTLY these options —
Boot device, Device Info, Factory reset, Sideload update, Power off. Recorded in
/home/lumen/questfree/docs/firmware/USB_UPDATE_MODE_MENU.md. NEVER ask Frank to read
this menu again. NEVER suggest options that aren't in that file (no 'Boot recovery', no
'View recovery logs' from this menu, no 'Switch slot').

## Wireless ADB pairing requires Wi-Fi on the host
(added ≤2026-08-13)

WIRELESS ADB PAIR REQUIRES WI-FI ON HOST: Android's `adb pair` (PIN or QR) requires the
pairing computer to be on Wi-Fi, not ethernet, not tailscale, not USB tether. Same
subnet is NOT enough. Lumen is ethernet-only and CANNOT wireless-pair with any Android
device. Use Thor (has Wi-Fi) or a USB cable. See
docs/sop/ANDROID_WIRELESS_ADB_REQUIRES_WIFI.md.

## Verify phone IP before network ops
(added ≤2026-08-13; cost of failure: 90 min of burned pair PINs on a wrong IP)

VERIFY PHONE IP BEFORE NETWORK OPS: never trust `tailscale status` for a peer's LAN
IP — the 'direct' field can be stale. Always query the device directly
(`adb shell ip -4 -o addr show`, or ask the user) and verify with ping + `ip neigh`
before firing network operations.
