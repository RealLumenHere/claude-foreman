# wq#1537 — deployed lumen-pr lagged origin/main while the primary sat on another branch

## What happened

wq#1536 merged `lumen-pr ffprimary` to origin/main (PRs #81/#82). `/home/lumen/bin` was
parked on `feat/wq1374-...` at the time (another lane's in-flight worktree, not this
tool's territory) — so the DEPLOYED `lumen-pr` kept running the pre-#1536 code, and any
`land` run with it would keep leaving primaries behind until the tree returned to `main`.

The sanctioned resync tool, `sync-live` (wq#1256), fast-forwards a primary's whole HEAD and
refuses outright on *any* dirty tracked file anywhere in the repo — correct for its job (a
wrong ref move is worse than a stale tree), but it means one unrelated ticket's uncommitted
WIP blocks every other file's deploy too. By the time this ticket ran, `/home/lumen/bin` had
exactly that: `wq`, `power-remediate`, and six other files carried uncommitted hunks from
wq#1498 and neighbors (tracked separately as wq#1538 — "deployed wq carries 9 tickets of
live-only hunks"). `sync-live` could not move regardless of what this ticket did.

`lumen-pr` itself carried no local edits in that tree — it was simply stale, not dirty.

## The fix

A single-file deploy-sync tool, same "ensure script" shape as
`ops/wq1479-close-verdict/ensure_wq_refuse_stderr.py` and
`ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py`, except those hand-patch
known text anchors while this one takes the file whole from `origin/<branch>` — the source
of truth already exists there, reviewed and merged, so there is nothing to re-derive.

`ensure_primary_file_deploy_sync.py`:
- `--check` reports whether `<repo>/<path>` matches `origin/<branch>:<path>`.
- `--apply` refuses if the live file has an uncommitted local edit (the one guard that
  matters — that content exists nowhere else), otherwise backs it up, writes a verified
  candidate (syntax-checked before it ever reaches the live path) with the target's
  executable bit, and rolls back if a post-write re-check fails.
- Never touches HEAD, the branch, or any other path — the class of deploy step wq#1537 asked
  for: one that does not depend on the primary's HEAD (wq#1313 lineage).

Defaults target `/home/lumen/bin` + `lumen-pr` + `origin/main`; every part is an env var
(`WQ1537_REPO`/`WQ1537_PATH`/`WQ1537_BRANCH`/`WQ1537_REMOTE`) or a flag, so the same tool
covers the next occurrence of this class without editing the script.

```
python3 ops/wq1537-primary-file-deploy-sync/ensure_primary_file_deploy_sync.py --check --json
python3 ops/wq1537-primary-file-deploy-sync/ensure_primary_file_deploy_sync.py --apply
python3 -m pytest harness/tests/test_wq1537_primary_file_deploy_sync.py
```

## Non-goals

This does not resync the whole `/home/lumen/bin` tree, and does not touch the wq#1538
backlog (`wq` and the other 8 dirty files) — that is a properly-landed-PR job, not a
live-file overwrite, and is out of scope here on purpose.
