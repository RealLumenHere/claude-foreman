# wq#1774 — deploy report

Deployed live via `/home/lumen/bin/lumen-run --root` (ISSUE-LOG #320 pattern),
not staged-only, per this ticket's ask.

## Applied

- `wait-for-port` and `unit-drift` installed to `/home/lumen/bin` (root-owned,
  executable) — see `deploy.sh:16-17`.
- 5 units from wq#1659's `units.diff` (port corrected to 5433 for
  `lumen-research-index`, per wq#1765) — `deploy.sh:19-49`.
- 2 units from wq#1771 (`lumen-brain-health`, `lumen-predicate-audit`) — dangling
  `After=...litellm.service` token dropped, no docker gate needed —
  `deploy.sh:51-55`.
- `systemctl daemon-reload`, `systemd-analyze verify` per unit, and an
  is-active check on the 3 running fleet units — `deploy.sh:57-66`.

## Verified

- `python3 /home/lumen/bin/unit-drift check` → `CLEAN (350 units, 198
  drop-ins, 307 enable-links)`, exit 0 — 0 dangling targets fleet-wide.
- `systemctl is-active lumen-fleet-cerebras lumen-fleet-mistral
  lumen-fleet-openrouter` → `active active active` (unaffected by the
  daemon-reload + unit edits, as expected).
- `deploy.sh` re-run end-to-end via `lumen-run --root` a second time to
  confirm idempotency (sed edits guarded by `grep -q`, `install -m` no-ops on
  matching content) — exit 0, no changes on the second pass.

## Note: unit-drift snapshot must run as `lumen`, not `--root`

`/home/lumen/fleet-units` is a `lumen`-owned git repo (dropins/units
directories, not `/etc`). Running `unit-drift snapshot` under `lumen-run
--root` writes root-owned files into it and then fails `git commit` with
"detected dubious ownership" (git's cross-euid safety check triggers whenever
the process euid differs from the repo directory's owning uid, independent of
which one is more privileged). Fix applied: `chown -R lumen:lumen
/home/lumen/fleet-units`, then re-ran `unit-drift snapshot` as plain `lumen`
— committed clean as `afa213e`. `deploy.sh` deliberately stops short of
calling `unit-drift snapshot` under `--root` to avoid repeating this.
