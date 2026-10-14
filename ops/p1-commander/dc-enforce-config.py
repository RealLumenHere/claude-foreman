#!/usr/bin/env python3
"""Enforce Desktop Commander canonical config on every gateway start.
P1 copy of /home/lumen/bin/dc-enforce-config.py -- BLOCKED/FORCE are
byte-identical to Lumen's live tier-1 enforcer (verified 2026-09-26) so
every control-plane node refuses the same irreversible/host-availability
ops regardless of which one is currently active. Only CFG's path differs
(this node's own home instead of /home/lumen). Wired as ExecStartPre of
dc-gateway.service.

TO ROLLBACK to zero restrictions: see Lumen's own
dc-enforce-config.py.bak.20260717_001558_pre_tier1 for the pre-tier1 shape;
do not hand-edit BLOCKED without also updating Lumen's copy (parity).
"""
import json
from pathlib import Path

CFG = Path.home() / ".claude-server-commander" / "config.json"

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
    "allowedDirectories": [],              # keep broad FS operability (Frank operator path)
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
