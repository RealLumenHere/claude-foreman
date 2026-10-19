#!/usr/bin/env python3
"""Enforce Desktop Commander canonical config on every gateway start (MAKSOOR copy of
/home/lumen/bin/dc-enforce-config.py, wq#4331 — same parity requirement wq#2607/wq#2608
applied to Anubis/Bes). Only CFG changed (maksoor home instead of lumen home); BLOCKED/
FORCE are byte-identical to Lumen's live tier-1 enforcer (fetched 2026-10-19).

blockedCommands = irreversible / unrecoverable-data / host-availability ops only. None
of this ticket's named RE tools (mtkclient, adb, rkdeveloptool) match any BLOCKED
basename, so they run unrestricted; `dd` stays blocked per existing fleet USB policy
(Balena Etcher only) and is not a tool this ticket needs.

TO CHANGE the blocklist:
  cp /home/maksoor/bin/dc-enforce-config.py /home/maksoor/bin/dc-enforce-config.py.bak.$(date +%Y%m%d_%H%M%S)
  <edit BLOCKED below>
  sudo systemctl restart dc-gateway
"""
import json
from pathlib import Path

CFG = Path("/home/maksoor/.claude-server-commander/config.json")

# Tier-1: irreversible / unrecoverable-data / host-availability ops only.
# Every entry blocks by basename(argv0) per segment. Reversible ops are NOT here.
BLOCKED = [
    "dd",                                  # raw block writes (USB policy = Balena only anyway)
    "mkfs", "mkfs.ext2", "mkfs.ext3", "mkfs.ext4",
    "mkfs.xfs", "mkfs.btrfs", "mkfs.vfat", "mkfs.fat", "mkfs.ntfs",
    "wipefs",                              # signature wipe
    "shred",                               # unrecoverable file destruction
    "fdisk", "sgdisk", "parted",           # partition table edits
    "reboot", "shutdown", "poweroff", "halt",  # host availability (no-reboot rule, enforced)
    "macchanger",                          # MAC spoof vector
]

FORCE = {
    "blockedCommands": BLOCKED,
    "pendingWelcomeOnboarding": False,     # the reset trigger -> off
    "telemetryEnabled": False,
    "allowedDirectories": [],              # keep broad FS operability (RE workstation path)
}


def main():
    CFG.parent.mkdir(parents=True, exist_ok=True)
    cfg = {}
    if CFG.exists():
        try:
            cfg = json.loads(CFG.read_text() or "{}")
        except Exception:
            cfg = {}
    cfg.update(FORCE)
    CFG.write_text(json.dumps(cfg, indent=2))
    print(f"[dc-enforce:tier1] blockedCommands={len(cfg.get('blockedCommands'))} "
          f"onboarding={cfg.get('pendingWelcomeOnboarding')}")


if __name__ == "__main__":
    main()
