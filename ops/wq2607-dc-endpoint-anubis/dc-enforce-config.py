#!/usr/bin/env python3
"""Enforce Desktop Commander canonical config on every gateway start (ANUBIS copy of
/home/lumen/bin/dc-enforce-config.py, wq#2607 — parity requirement in
MCP_CONTROL_PLANE_HA_DESIGN.md §3.2 "dc-enforce-config parity"). Only CFG changed
(anubis home instead of lumen home); BLOCKED/FORCE are byte-identical to Lumen's live
tier-1 enforcer (verified via `fleet-exec run lumen -- cat /home/lumen/bin/dc-enforce-config.py`,
2026-09-25) so both nodes refuse the same irreversible/host-availability ops.
Wired as ExecStartPre of dc-gateway.service (see dc-gateway.service in this directory).
"""
import json
from pathlib import Path

CFG = Path("/home/anubis/.claude-server-commander/config.json")

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
