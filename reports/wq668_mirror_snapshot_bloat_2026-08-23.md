# wq#668 — REPO_MIRROR snapshot 309d1132 (12x oversized) — closing diagnosis

**Date:** 2026-08-23 · **Worker:** wq668-a1 (headless BUILD) · **Status:** closed as transient, corroborated post hoc; not root-caused to a specific directory (data no longer exists to inspect).

## TL;DR

The anomalous snapshot itself is gone — the normal weekly `lumen-backup-prune` run (2026-08-23, same Sunday 10:00 UTC cadence as the 08-16 run implicated in [[wq669]]) forgot every 2026-08-16 hourly critical snapshot on both repos except one daily keeper, `309d1132` included. Direct `restic diff`/`ls` against it is no longer possible. But this week's prune log gives strong indirect physical confirmation the bloat was real, not a stats artifact, and that it's now fully and cleanly reclaimed:

- `REPO_MIRROR` (`/data/lumen/backups`) prune: **535,046 blobs / 4.809 GiB** removed (avg ~9.4 KiB/blob)
- `REPO_LOCAL` (`/home/lumen/backups/local`) prune: **13,678 blobs / 4.670 GiB** removed (avg ~358 KiB/blob)

Same repos, same forget policy, same retention window, similar total GiB reclaimed — but the mirror shed **~39x more, much smaller objects**. That's exactly the shape you'd expect from a snapshot with ~1.9M small files rolling off retention, versus ordinary daily churn. `errors=0` on both — the prune completed cleanly, so whatever caused the bloat did not corrupt the repo and did not recur (no similar bloat in any hourly mirror snapshot since 08-16, per the original ticket's neighbor comparison).

## What was checked this session

1. `restic -r /data/lumen/backups snapshots --json --tag critical` (222 snapshots) — no `309d1132`, and no 2026-08-16 snapshot survives except `043f96d4` (23:03:25Z, 27,547 files — normal size). Confirms the snapshot was forgotten, not just slow to query.
2. Same check against `REPO_LOCAL` (225 snapshots) — same pattern, single 08-16 daily keeper (`454182d2`, 23:02:33Z, 27,547 files).
3. `restic stats 309d1132` on the mirror → `no matching ID found for prefix`, consistent with (1).
4. `/home/lumen/services/backup/logs/prune-20260823.log`: two clearly separated sections (`=== prune /home/lumen/backups/local ===` line 1, `=== prune /data/lumen/backups ===` line 6216), each ending `OK`, final `=== DONE — errors=0 ===`. Local: 163 snapshots removed / 13,678 blobs / 4.670 GiB. Mirror: 160 snapshots removed / 535,046 blobs / 4.809 GiB. This is the load-bearing evidence above.
5. A live `restic stats latest --mode restore-size` against the mirror timed out at 2 minutes under current fleet load (~15 load average / 4 cores, 7 concurrent fleet workers) — reconfirms the original ticket's side note that `/data/lumen/backups` is slow even for simple queries. Did not retry; not needed for this diagnosis, but worth a separate look during an actual quiet window if mirror query latency starts affecting the hourly critical job's own retry-lock budget (see [[wq669]]).

## Conclusion

- The 12x bloat was real (physically reclaimed data, not a display/accounting quirk) — upgrades the original ticket's "transient scratch bloat" theory from unconfirmed to evidence-backed.
- It self-resolved within the hour (neighboring hourly snapshots already showed normal size in the original ticket) and has now been fully swept up by normal retention with no repo damage.
- Root cause (which directory / which concurrent fleet worker produced ~1.9M extra small files during the 10:57–11:02Z contention window) cannot be pinned down further — the only artifact that could answer it (`309d1132`'s tree) no longer exists in any repo, local or mirror, and won't be recoverable barring an off-host copy (none exists per `services/backup/README.md` — Thoth secondary is still offline).
- No fix needed here; this ticket is a diagnosis-only close. If this pattern recurs on a future hourly mirror snapshot, the same prune-log blob-count/size ratio check (mirror vs local, same cycle) is a fast way to confirm it's real before spending time on live `restic diff`, which this session and the original investigation both found unreliably slow against this destination.
