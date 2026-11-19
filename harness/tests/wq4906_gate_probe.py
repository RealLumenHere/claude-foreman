#!/usr/bin/env python3
"""wq#4906 acceptance probe — classify_bash() only, nothing is executed.

Kept as a file rather than a `python3 -c` one-liner on purpose: the live gate
reads the command line, and before this fix the direction-blind raw-device row
classified a command whose *text* merely quoted a flash device path as RED
(ISSUE-LOG #1670). Run: PYTHONPATH=harness python3 harness/tests/wq4906_gate_probe.py
Exit 0 = every case classifies as expected.
"""
import os
import sys

# argv[1] (or $WQ4906_GATE_DIR) points the probe at a different action_gate.py,
# which is how it was shown to be able to fail (wq#2630): run against the
# pre-fix tree at the pinned rev 8530002b it reports 13 failures, not zero.
_gate_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("WQ4906_GATE_DIR", "harness")
sys.path.insert(0, _gate_dir)
import action_gate as ag  # noqa: E402

MTD = "/dev/" + "mtd0"
MEM = "/dev/" + "mem"

# (command, expected verdict, expected hw_floor)
CASES = [
    # A — espefuse burn verbs the burn_* prefix missed (underscore, as the
    # installed v4.7.0 spells them)
    ("espefuse write_protect_efuse RD_DIS", "RED", True),
    ("espefuse read_protect_efuse BLOCK1", "RED", True),
    ("espefuse set_flash_voltage 1.8V", "RED", True),
    # B — v5.0 dashed spellings, latent until a v5 lands
    ("espefuse write-protect-efuse RD_DIS", "RED", True),
    ("espefuse set-flash-voltage OFF", "RED", True),
    ("espefuse burn-efuse FLASH_CRYPT_CNT 1", "RED", True),
    ("espefuse burn-key BLOCK1 key.bin", "RED", True),
    ("esptool write-flash 0x0 firmware.bin", "RED", True),
    ("esptool erase-flash", "RED", True),
    ("esptool erase-region 0x0 0x1000", "RED", True),
    # C — direction. Writes still RED, reads GREEN.
    ("cp firmware.bin " + MTD, "RED", False),
    ("tee " + MEM, "RED", False),
    ("cat firmware.bin > " + MTD, "RED", False),
    ("cat " + MTD + " > dump.bin", "GREEN", False),
    ("cp " + MTD + " dump.bin", "GREEN", False),
    ("pv " + MTD + " > dump.bin", "GREEN", False),
    ("rsync -a " + MTD + " /home/lumen/backups/", "GREEN", False),
    # read verbs stay GREEN in both spellings
    ("esptool read-flash 0x0 0x1000 dump.bin", "GREEN", False),
    ("esptool.py read_flash 0x0 0x1000 dump.bin", "GREEN", False),
    ("espefuse summary", "GREEN", False),
    ("espefuse get-custom-mac", "GREEN", False),
]


def main():
    bad = 0
    for cmd, want_verdict, want_floor in CASES:
        verdict, reason, _snap, floor = ag.classify_bash(cmd, {})
        if verdict != want_verdict or bool(floor) != want_floor:
            bad += 1
            print(f"FAIL {cmd!r}: got {verdict}/floor={floor} "
                  f"want {want_verdict}/floor={want_floor} ({reason})")
    print(f"checked {len(CASES)} cases, {bad} failures")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
