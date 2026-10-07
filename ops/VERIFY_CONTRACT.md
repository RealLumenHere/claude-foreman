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
  vflag      /home/lumen/bin/bar --fail-on-drift              # a flag the payload CALLS
  vdone
fi
# ... install steps ...
```

### Files that live in a git-tracked tree

`vfile` passes a target that is byte-identical to the bundle copy. It also passes one
that has moved on from it: the target is tracked in its own git repo (`/home/lumen/bin`,
`/home/lumen/minerva`, `kb`), matches that repo's HEAD, and some commit in the file's
history held the bundle copy byte for byte. It prints `OK superseded:` for that case.
Before wq#3468, every bundle older than the last edit to a shared file failed every day
and `ops-deploy-drift` re-filed it. Redeploying it would have reverted the later commits.
An uncommitted edit on the live file is still a MISS, and so is a bundle copy edited
after it shipped.

### Bundles whose payload calls another bundle's binary

Byte-matching your own files proves nothing about a tool you shell out to. wq#3228 found
`ops/wq1747-litellm-key-rotation` gating a rotation on
`stale-env-audit --fail-on-drift ... --vars ...` against an installed build that implemented
neither flag — and because that build scanned argv by substring, it ignored both and exited
0 on a whole-fleet audit. A gate that cannot fail. `vflag BIN FLAG` asks the callee's own
`--help` what it implements; a build too old to have the flag is a MISS, not an ERR.
`wq1747-litellm-key-rotation` and `wq3215-stale-env-audit-timer` are the worked examples
(wq#3245). A bundle that installs nothing at all still needs a handler for this reason.

Helpers are in `ops/_verify.sh`. Reference bundles: `wq900-primary-tree-guard`
(units + enabled + active) and `wq1658-cron-stamp-dropin` (binary + a patched file).

### Bundles that do not install onto Lumen

A bundle whose payload lands on another node (`wq2286-gitea-secret-scan` on Anubis,
`wq2472-vlogs-fleet-rollout` on seven nodes) cannot be checked locally at all — a
local-only handler reports it absent however live it is. Use `vremote_up HOST`,
`vremote_file HOST SRC DST` and `vremote_unit HOST UNIT`, and gate each node on
`vremote_up` so an unreachable node is **ERR (exit 2), never MISS**. "I could not
look" must not read as "it is not deployed" (wq#2985).

### Bundles that are retired or superseded

Keep the bundle and give it a handler with the sense inverted: assert the
replacement is live and the retired names stayed absent, then refuse to install
behind a `WQ<n>_FORCE_*` env var. `wq858-trivy-weekly-scan`, `wq1299-phoenix-tracer`
and `wq1396-minerva-proposal-promoter` are the worked examples. Without this, the
next content-index audit reads the bundle as ABSENT and files it for deploy — which
for wq1396 would have double-filed every CVE proposal.

### ABSENT is not a disposition

A content index (`ops-deploy-drift`) reports ABSENT for anything it did not find,
including bundles that install outside its 13 roots, on another node, or under a
renamed unit. Never deploy on an ABSENT verdict alone; pair it with a live signal
the index cannot fake. `ops/DEPLOY_TRIAGE.md` is the register that rule produced.

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

wq#1732 audited the existing bundles; wq#2985 triaged the 36 it left untracked and
added the 23 missing handlers, so all 36 now answer `--verify`. Dispositions and
the evidence behind each are in `ops/DEPLOY_TRIAGE.md`, generated from
`ops/deploy_triage.py`. `python3 ops/deploy_triage.py --check` re-derives every
live signal and exits 1 when one has rotted.

For any bundle still without a handler: add one, then confirm with
`bash ops/<bundle>/install.sh --verify`.
