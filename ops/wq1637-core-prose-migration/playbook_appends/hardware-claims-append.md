Target: /home/lumen/instructions/playbooks/hardware-claims.md — append at end of file.
Source ticket: wq#1637, matches instincts_candidates.yaml's `verify-inventory-before-spend`.

---

## VERIFY INVENTORY BEFORE RECOMMENDING SPEND (claude_memory.md, 2026-07-16)
Any report recommending hardware spend MUST verify existing inventory first (`lsblk`,
`lumen_state.py current lumen storage`, or the equivalent for the component in question) —
never price a purchase off an assumed baseline. wq#110 priced an NVMe purchase off an assumed
SATA-only baseline without checking `lsblk` first.
