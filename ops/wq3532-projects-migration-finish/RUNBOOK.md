# wq#3532 — finish the /home/lumen/projects → /scratch/fleet/projects migration

**State (recon 2026-10-07, worker session + ticket record):** wq#2094 moved project
dirs to `/scratch/fleet/projects` but created `/home/lumen/projects/projects ->
/scratch/fleet/projects` (`ln -s` into an existing dir) instead of converting the
root. wq#3473's stopgap added 4 compat shims
(`/home/lumen/projects/{bench,superpowers-chrome,lumen-copilot,03045bd4}`).
`/home/lumen/projects` still holds real residents: `auto-browser`, `spectrio-tf1`
(active, mtime 2026-10-06), `_handoffs/`.

Live old-path refs, confirmed via `systemctl cat` 2026-10-07 (8 refs, 5 units):

| unit | refs | active |
|---|---|---|
| lumen-bench.service | WorkingDirectory, BENCH_DB, ReadWritePaths | yes |
| lumen-bench-checker.service | BENCH_DB, ExecStart | timer-driven |
| lumen-node-killer.service | ExecStart (runs as **root**) | yes |
| lumen-copilot-status.service | WorkingDirectory | yes |
| chrome-mcp.service | ExecStart | no (inactive) |

Drop-ins (`*.service.d/*.conf`) carry no old-path refs — replacing the main unit
files preserves them.

## Decision: option (a) — rewrite unit refs to the canonical root, remove shims

Ticket offered (a) rewrite unit refs, or (b) move the last residents and turn
`/home/lumen/projects` into a single symlink. **(a)** because:

1. **(b)'s only required filesystem op is the risky one.** `spectrio-tf1` is active
   today; moving it breaks any process holding a cwd/open fd there — the exact
   "cwd deleted → HTTP 500" failure this ticket exists to close out. (a) moves
   nothing that is live.
2. **systemd sandboxing resolves symlinks at namespace setup.** With (b),
   `ReadWritePaths=/home/lumen/projects/bench` works only through the link; a later
   link change silently invalidates the sandbox allowlist. Units should name real
   paths.
3. **(b) keeps two names for every project path forever**, so new units/scripts
   keep being written against the deprecated root and the class never dies. (a)
   converges on one canonical root; `/home/lumen/projects` stays a plain dir for
   what genuinely still lives there.
4. **(b)'s dir→symlink swap is the same shape as the original mistake** (replace a
   non-empty dir with a link, atomically, while things run) — wq#2094 already
   fumbled it once.

## Apply

The worker sandbox (foreman worktree + add-dirs) cannot write `/etc/systemd/system`,
`/home/lumen/projects`, or `/home/lumen/fleet-units`, so the change is staged here
and applied by a privileged session ON Lumen:

```
sudo bash /home/lumen/foreman/ops/wq3532-projects-migration-finish/apply.sh
```

The script: precondition-checks canonical targets exist → backs up live units →
installs `staged/*.service` → daemon-reload + restart → **verifies** (is-active,
bench `:8135` serves, bench-checker oneshot exits 0, zero `/home/lumen/projects/`
refs left in `/etc/systemd/system`) → only then removes the 4 shims + the nested
`projects/projects` link → re-verifies with shims gone (catches symlink-masked
breakage) → syncs the `/home/lumen/fleet-units` baseline repo → sweeps lumen/root
crontabs + `/etc/cron.d` for the class (this sandbox's `crontab -l` was not
allowlisted). chrome-mcp stays inactive; its file is migrated for next start.

**Rollback:** `sudo bash apply.sh rollback <backup-dir>` (restores units, recreates
shims, restarts). bench.db backup from the stopgap:
`/home/lumen/backups/bench.db.pre-b2b.20261007_062841`.

## Scan: /home/lumen/bin (ticket's unscanned scope) — 4 real hits, follow-up filed

| file:line | ref | class? |
|---|---|---|
| fleet-search:39 | `PROJECTS = "/home/lumen/projects"` per-project cache root | **yes** — `--project bench` would mkdir-resurrect the old path once shims are gone; needs dual-root resolve |
| gitea-rotate-finish:32 | default `REPOS` includes `/home/lumen/projects/lumen-messenger-cc` | **yes** — verify where lumen-messenger-cc lives now |
| wq:2163 | `_sha_repo_candidates` base list | mild — fail-soft, but should also scan `/scratch/fleet/projects` |
| project-handoff:26 | `_handoffs/` under old root | no — `_handoffs` stays a real resident under (a) |
| project-touch:21, ingest_device_re.py:16 | `projects.json` / prose | no — not the dir |

These are lumen-bin repo changes, out of this foreman PR's scope — filed as a
follow-up wq ticket (see done notes).
