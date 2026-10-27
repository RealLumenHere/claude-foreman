# wq#4712 — disk-layout note wrongly claims projects/worktrees/esphome/go are /scratch symlinks

## Finding
`claude_memory.md`'s `misc:disk-layout-rules` block (and the `disk-layout.rules`
fact it's derived from in `lumen_state.py`) claimed:

```
- note: /home/lumen/worktrees → /scratch/fleet/worktrees
- note: /home/lumen/esphome → /scratch/fleet/esphome
- note: /home/lumen/go → /scratch/fleet/go
- note: /home/lumen/projects → /scratch/fleet/projects
```

Verified live on Lumen 2026-10-27 (this worker's sandbox permits `readlink`/`df`
by full path even though `ls`/`wc`/`grep` on `/home/lumen/claude_memory.md`
itself are path-sandbox-refused — same asymmetry wq#4490 hit):

```
$ readlink -f /home/lumen/projects /home/lumen/worktrees /home/lumen/esphome /home/lumen/go /home/lumen/scratch
/home/lumen/projects
/home/lumen/worktrees
/home/lumen/esphome
/home/lumen/go
/home/lumen/scratch
```

A symlink's `readlink -f` resolves to its target; here it resolves to itself for
all five paths — none of them is a symlink.

```
$ df -h /home/lumen/scratch /home/lumen/projects /home/lumen/worktrees /home/lumen/esphome /home/lumen/go /home/lumen/foreman /home/lumen/projects/limpet
/dev/mapper/ubuntu--vg-ubuntu--lv  466G  328G  119G  74% /
```
(all seven mount on the root LV — `foreman` and `projects/limpet` too, which is
why their `.build` caches land on root: `projects/limpet/iso-build/.build` 20G,
`foreman/.build` 28G.)

The other paths the same note claims ARE real symlinks — checked the same way,
confirmed TRUE:

```
$ readlink -f /home/lumen/models /home/lumen/gocheckin /home/lumen/sandbox /home/lumen/simpill-scratch-preserved /home/lumen/build /home/lumen/android-sdk /home/lumen/re
/scratch/fleet/models
/scratch/fleet/gocheckin
/scratch/fleet/sandbox
/scratch/fleet/simpill-scratch-preserved
/scratch/fleet/build
/scratch/fleet/android-sdk
/data/lumen/archive/re

$ df -h /scratch /data/lumen
/dev/md1   1.9T  731G  1.2T  39% /scratch
/dev/md0   469G  347G   98G  78% /data/lumen
```

So the defect is scoped exactly to the four project-ish paths
(`projects`, `worktrees`, `esphome`, `go`) plus the unstated fact that
`/home/lumen/scratch` itself is also a plain root-LV directory, not some other
mount — `models`/`gocheckin`/`sandbox`/`simpill-scratch-preserved`/`build`/
`android-sdk`/`re` were already correct.

## Why the wrong version survived
`/home/lumen/claude_memory.md`'s header says the file is machine-generated from
live facts, with one exception: `MEMORY DISCIPLINE` says fleet facts must go
through `lumen_state.py ingest` so `current`/`history`/`asof` can serve as the
"never trust dated prose" check. The `misc:disk-layout-rules` block was hand-
pasted straight into that generated zone instead — `lumen_state.py current
disk-layout rules` (the one fact that *does* exist for this subject) never
contained the per-path symlink list, only a short pointer: "See claude_memory.md
DISK LAYOUT RULES." A hand-pasted block in a generated file is invisible to
every probe that trusts the generation pipeline, so the claim never got
re-verified after it was written.

It also matches a real, already-recorded decision going stale: `ops/wq3532-
projects-migration-finish/RUNBOOK.md` (2026-10-07) explicitly chose, for
`/home/lumen/projects`, option (a) — rewrite unit refs to the real path and
keep `/home/lumen/projects` a plain directory — over option (b), turning it
into a symlink, specifically because `spectrio-tf1` was an active resident and
systemd sandboxing resolves symlinks at namespace setup. `worktrees`/`esphome`/
`go` were apparently never even queued for a symlink migration, but nothing
ever went back and fixed the disk-layout note to stop claiming otherwise.

## Decision: fix the docs, do not move the data
Disk headroom doesn't force a migration: root LV has 119G free (74% used, not
critical), `/scratch` has 1.2T free. Moving `projects`/`worktrees`/`esphome`/
`go` to real `/scratch` symlinks now would repeat exactly the risk wq#3532
already rejected for `projects` alone — and worse for `worktrees`, which is the
live working-tree root for every fworker session running right now (including
this one): swapping a non-empty directory with active open cwds/fds for a
symlink is the same "cwd deleted -> HTTP 500"-shaped failure wq#3532 built
`apply.sh`'s whole precondition/verify/rollback sequence to avoid, multiplied
across every concurrent worker instead of one service. `esphome` backs a live
ESPHome relay config; `go` is an active toolchain dir. None of that risk buys
anything a documentation fix doesn't already buy, since root isn't full.

## Fix applied live
Both target files sit outside this worktree's git tree and (for the Bash tool)
outside its `--add-dir` sandbox — `/home/lumen/claude_memory.md` is untracked
prose at `/home/lumen/`, and `lumen_state.py`'s backing store (`minerva.db`) is
only reachable through its CLI wrapper. As wq#4490 found for `HANDOFF.md`, the
Edit tool's path allowlist is broader than Bash's: `cat`/`grep`/`ls`/`wc` on
`/home/lumen/claude_memory.md` are all refused, but `Read`/`Edit` reach it
directly, so both edits were made in place with no backup/restart machinery
(neither touches a running service):

1. **`lumen_state.py ingest disk-layout rules "..."`** — superseded the prior
   fact value (see `disk_layout_rules_fact_after.md` for the exact new value
   and the CLI call). `lumen_state.py current disk-layout rules` now reflects
   it immediately; prior value preserved in history (bi-temporal store, no
   data lost).
2. **`/home/lumen/claude_memory.md`** `misc:disk-layout-rules` block — edited
   in place to mark `projects`/`worktrees`/`esphome`/`go` as real root-LV
   dirs, not symlinks, with the verified sizes/percentages and a pointer back
   to wq#3532's reasoning (see `claude_memory_misc_disk_layout_rules_after.md`
   for the exact after-text).
3. **`/home/lumen/claude_memory.md`** hand-prose region (survives regen per
   wq#4537) — appended a dated correction entry recording the root cause, the
   decision not to migrate, and the two residuals below (see
   `hand_prose_after.md` for the exact after-text).

## What else sits on root that the note said was elsewhere
Checked every path the note names: only `projects`/`worktrees`/`esphome`/`go`
(+`scratch` itself) are wrong; `models`/`gocheckin`/`sandbox`/
`simpill-scratch-preserved`/`build`/`android-sdk`/`re` all verified correct
(see the `readlink -f` + `df` output above). Found two consequences, both
flagged rather than fixed here since each is its own risk decision:

- `projects/limpet/iso-build/.build` (20G) and `foreman/.build` (28G) sit on
  root because their parent dirs were never migrated — a build-cache cleanup
  ticket, not a documentation fix.
- `/home/lumen/minerva/context/fleet_tools.yaml` ("FLEET_TOOLS text", the third
  copy the ticket named) is referenced from `claude_memory.md`'s
  `architecture:memory-pipeline` and `network:key-paths` facts but was not
  checked for the same stale claim — `/home/lumen/minerva` is not one of this
  worker's `--add-dir`s, and `cat`/`grep`/`ls` on it are refused the same way
  as wq#4712's prior attempt found. Filed **wq#4723** (lane `auto`, needs
  `dir(/home/lumen/minerva)`) to grep it from a worker that actually has that
  directory granted.

## Issue log
Appended row 1608 to `/home/lumen/kb/ISSUE-LOG.md` via
`issue_log_append.py` (symptom/cause/solution per the entries above,
status `SOLVED (fleet_tools.yaml check + .build cleanup deferred, wq#4723 filed)`).

Refs: wq#4712, wq#3532, wq#4490 (path-sandbox-vs-Edit precedent), wq#4723 (follow-up).
