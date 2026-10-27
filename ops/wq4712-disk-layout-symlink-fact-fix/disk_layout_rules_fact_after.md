Prior value of the `disk-layout.rules` fact (`lumen_state.py current disk-layout rules`,
since 2026-10-02):

```
Root=OS+programs ONLY. /scratch (md1 1.6TB)=active work. /data/lumen (md0 490GB)=project data. /mnt/lumen-backup (sda1 1.8TB)=archives. NEVER dump data to root. See claude_memory.md DISK LAYOUT RULES.
```

Call made (2026-10-27, wq#4712):

```
lumen_state.py ingest disk-layout rules "Verified 2026-10-27 (wq#4712) via readlink -f + df: ..." \
  --source "wq#4712 readlink -f + df verify" \
  --source-kind observed \
  --note "wq#4712: prior value wrongly implied via claude_memory.md pointer that projects/worktrees/esphome/go are /scratch symlinks; they are real dirs on root LV. That symlink claim was hand-pasted into claude_memory.md's generated zone, bypassing the lumen_state ingest pipeline, so no probe ever caught the drift."
```

Result: `{'action': 'superseded', 'subject': 'disk-layout', 'predicate': 'rules', ...}` —
new value now states / is root-LV OS+packages PLUS the four unmigrated real dirs
(projects/worktrees/esphome/go/scratch), confirms which paths ARE true /scratch or
/data symlinks, and names the two consequences (projects/limpet/iso-build/.build 20G,
foreman/.build 28G). `lumen_state.py current disk-layout rules` now returns this
corrected value; the prior value is retained in bi-temporal history, not deleted.
