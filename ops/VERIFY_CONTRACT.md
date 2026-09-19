# ops bundle deploy-verify contract (wq#1696)

A fix that lives outside a lane repo (bin/, minerva/, /etc/systemd) ships as a bundle
here: `ops/wqNNNN-<slug>/` with an `install.sh`. Merged is not live. A bundle is live
only when its own `install.sh --verify` says so on the box.

## The rule

Every new `install.sh` answers `--verify`:

- **Read-only.** No writes, no sudo, no systemctl start, enable or reload. It must be
  safe to run from a timer as the lumen user.
- **exit 0** only when every live target matches what the bundle ships.
- **exit 1** when anything is missing or differs, one `MISS <what>` line each.
- **exit 2** when the check itself could not run.
- The handler is a literal `if [ "${1:-}" = --verify ]; then` (or a `--verify)` case
  arm) placed **before** any install step. Tooling only runs `--verify` on bundles
  where it sees that marker, because a script that does not know the flag would
  ignore it and install.

## Template

```bash
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = --verify ]; then
  . "$SRC/../_verify.sh"
  vfile      "$SRC/foo.py"    /home/lumen/bin/foo              # byte-identical
  vunit_file "$SRC/foo.timer" /etc/systemd/system/foo.timer
  venabled   foo.timer
  vactive    foo.timer
  vcontains  /home/lumen/bin/unit-drift "foo-hook"            # an edit, not a copy
  vdone
fi
# ... install steps ...
```

Helpers are in `ops/_verify.sh`. Reference bundles: `wq900-primary-tree-guard`
(units + enabled + active) and `wq1658-cron-stamp-dropin` (binary + a patched file).

## What consumes it

- **Liveness probe** `ops_verified(<bundle>)` (harness/liveness.py). It runs the
  primary tree's copy of the bundle, never a worktree's.
- **[verify] tickets** (orchestrator/judgment.py `file_verify_pass`). When the shipped
  ticket cites a bundle with a handler, the verify ticket carries
  `Liveness: ops_verified(<bundle>)`. When a cited bundle has no handler, the ticket
  says so, and a repo-only check does not count.
- **`/home/lumen/bin/ops-deploy-drift`**. A daily sweep runs `--verify` on every
  bundle that has a handler and opens one keyed aq row (`ops-drift:<bundle>`) per
  bundle that fails for more than 24h. Bundles without a handler are only reported
  heuristically, never alerted.
- **`wq done`** (wq#1724) already refuses to close a ticket whose artifact is an
  `ops/*/install.sh` without `--deployed` byte-match proof or `--no-deploy-needed`.

## Backfill

wq#1732 audits the existing bundles. For each bundle that should stay live, add a
`--verify` handler, then confirm with `bash ops/<bundle>/install.sh --verify`.
