## What
Deploy record for the Minerva meta-review synthesis stage: `ops/wq1927-meta-review/` ships the deployed `minerva_verify.py` plus a change-safety replay script (`install.sh`: backup -> apply -> import-health -> auto-revert).

## Why
The code change lives in the minerva repo (PR #18, merged, live tree fast-forwarded). This branch records the deployment in the foreman repo per the wq#1724 ops-install evidence shape, so the evidence gate can byte-match the live file against a branch-tracked artifact.

## How
Artifact copied from the merged minerva branch; verified sha256-identical to `/home/lumen/minerva/minerva_verify.py` post-deploy. `install.sh` is a genuine re-apply path for a host that missed the git fast-forward.

## Testing
`fworker.py verify-evidence --ticket 1927` returns ok:true (citation in diff + deploy_target byte-match). Minerva-side: `python3 test_meta_review.py` all green; live import health PASS; factory close health PASS (proposal f75013a36dcd -> deployed).

## Risk / rollback
Docs/ops artifact only in this repo; no runtime path reads it. Rollback = revert the commit. The deployed feature itself is off by default (`META_REVIEW_ENABLED=0`).

Refs: wq#1927

🤖 Generated with [Claude Code](https://claude.com/claude-code)
