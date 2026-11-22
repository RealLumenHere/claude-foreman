# wq#5245 — lumen-backup-critical failing every hourly run since 2026-11-18

## Symptom
`lumen-backup-critical.service` → `Result=exit-code`, status=2, every timer fire
(aq recurring open alert x52; LRA audit wq#4999). Journal only said
`FAILED restic backup to /mnt/lumen-backup/restic-local` and `... /data/lumen/backups`,
`errors=2`.

## Root cause (evidence)
restic's own output goes to `/home/lumen/services/backup/logs/critical-YYYYMMDD.log`,
not the journal. Every run there:

```
error: open /home/lumen/scratch/gate_corpus/.hidden/MANIFEST.hidden.json: permission denied
error: openfile for readdirnames failed: open /home/lumen/scratch/gate_corpus/.hidden/_builder: permission denied
error: openfile for readdirnames failed: open /home/lumen/scratch/gate_corpus/.hidden/cases: permission denied
error: open /home/lumen/scratch/gate_corpus/.hidden/last_run.json: permission denied
snapshot 0867ae63 saved
Warning: at least one source file could not be read
```

restic exits **3** (snapshot saved, some source unreadable), the script counts it as
a failure for both repos → `exit 2`. These are the only restic errors in the
11-18, 11-21 and 11-22 logs. Onset: `critical-20261117.log` has 0 such warnings and
24× `errors=0`, `critical-20261118.log` has 32; the `.hidden/*` entries are
`root:root 0700/0600` with mtimes 2026-11-18 07:11–07:20Z.

The root ownership is deliberate: `/home/lumen/scratch/gate_corpus/run_corpus.py:43`
— "hidden half is not readable by this user (by design: it is root-owned 0700)". It is
the hold-out set for model critics; `/home/lumen/scratch` is in `PATHS_TO_BACKUP`, so
the lumen-uid restic run walked straight into it.

## Fix (applied live 2026-11-22, backup `backup-critical.sh.bak.20261122_wq5245`)
`backup-critical.sh` (copy here, identical to live):
- `:253-259` — `--exclude "/home/lumen/scratch/gate_corpus/.hidden"`. Not backed up on
  purpose: pulling it into a lumen-readable restic repo (or staging it as lumen like
  `/etc/cloudflared`) would hand the hold-out to every lumen-uid agent and void it.
  Trade-off accepted: a disk failure loses the hold-out (a root-run backup would be
  the way to cover it; `.hidden/_builder` exists but is unreadable to this lane, so
  regenerability is unverified), whereas a leak silently voids the critic signal.
- `:293-296` — the FAILED line now carries `restic rc=<n>` and the log path, so the
  next failure is diagnosable from the journal alone.

The `/home/lumen/services` tree is not a git repo (ISSUE-LOG #917), so this directory
is the versioned record, following `ops/wq3717-a2-backup-important-fix/`.
`backup-important.sh` does not back up `/home/lumen/scratch` (no `scratch` match) —
not affected.

## Not in scope here
Ticket also listed Lumen `audit-rules.service`, maksoor `max-brightness.service`,
bes `grub-initrd-fallback.service` → split to wq#5247 (audit rules are root-only
readable; the other two are other nodes).
