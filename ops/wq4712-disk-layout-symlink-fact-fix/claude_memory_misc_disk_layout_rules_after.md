After text for `/home/lumen/claude_memory.md`, `### misc:disk-layout-rules` section
(edited live with the Edit tool 2026-10-27, wq#4712 — this file is not tracked by
`claude-foreman`, so this is the paper trail, not the edit itself):

```
### misc:disk-layout-rules
- note: CORRECTED 2026-10-27 (wq#4712, readlink -f + df verify): /home/lumen/projects, /home/lumen/worktrees, /home/lumen/esphome, /home/lumen/go, and /home/lumen/scratch itself are REAL DIRECTORIES on the ROOT LV (/dev/mapper/ubuntu--vg-ubuntu--lv, 466GB, 74% used, 119GB free) — NOT symlinks to /scratch/fleet/*. The lines below claiming otherwise were hand-pasted into this generated zone and never went through lumen_state.py ingest, so no probe caught the drift (see disk-layout.rules fact + hand-prose below). Consequence: projects/limpet/iso-build/.build (20G) and foreman/.build (28G) sit on root today.
- note: /scratch (md1, XFS, 1.9TB, 39% used) — active work: builds, models, sandbox, scratch data
- note: /data/lumen (md0, ext4, 469GB, 78% used) — project data: /data/lumen/archive/re (symlinked as /home/lumen/re)
- note: /mnt/lumen-backup (sda1, ext4, 1.8TB) — cold archives, backups, old projects
- note: / (root LV, 466GB, 74% used, 119GB free) — OS, apt packages, pip packages, /opt tools, systemd, configs, AND (not yet migrated) /home/lumen/{projects,worktrees,esphome,go,scratch}
- note: /home/lumen/re → /data/lumen/archive/re (TRUE symlink, verified)
- note: /home/lumen/models → /scratch/fleet/models (TRUE symlink, verified)
- note: /home/lumen/worktrees — real dir on root LV, NOT a symlink (verified 2026-10-27; wq#3532 kept /home/lumen/projects as a real dir for the same reason — active cwd/fd risk — and worktrees/esphome/go were never migrated either)
- note: /home/lumen/gocheckin → /scratch/fleet/gocheckin (TRUE symlink, verified)
- note: /home/lumen/sandbox → /scratch/fleet/sandbox (TRUE symlink, verified)
- note: /home/lumen/simpill-scratch-preserved → /scratch/fleet/simpill-scratch-preserved (TRUE symlink, verified)
- note: /home/lumen/build → /scratch/fleet/build (TRUE symlink, verified)
- note: /home/lumen/esphome — real dir on root LV, NOT a symlink (verified 2026-10-27)
- note: /home/lumen/android-sdk → /scratch/fleet/android-sdk (TRUE symlink, verified)
- note: /home/lumen/go — real dir on root LV, NOT a symlink (verified 2026-10-27)
- note: /home/lumen/projects — real dir on root LV, NOT a symlink (verified 2026-10-27; wq#3532 RUNBOOK option (a): converge units on this canonical real root rather than risk an atomic dir→symlink swap on a dir with live residents)
- note: If an agent needs a work directory, use /scratch/agent-work/<ticket-id>/
- note: Target /scratch or /data — NEVER root. For projects/worktrees/esphome/go specifically: they ARE root today, so a tool writing under them (e.g. a .build cache) still lands on root — redirect such output to /scratch explicitly.
- note: If a tool defaults to /home/lumen, redirect its output to /scratch and symlink if needed
```

Plus a new dated entry appended inside the `<!-- hand-prose:begin -->` / `<!-- hand-prose:end -->`
region (survives regen, wq#4537) — see README.md for the full text.
