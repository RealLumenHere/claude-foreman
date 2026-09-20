## What
Adds `ops/wq2100-kb-append/kb-append <branch>`, a sanctioned land path for the kb repo.

## Why
kb has no origin, so lumen-pr and sync-live cannot land it; worker kb row branches were stranded and their ISSUE-LOG rows never reached the primary.

## How
The helper resets or snapshot-commits the primary's uncommitted tracked backlog, rebases the branch onto kb master in a throwaway worktree, then ff-merges the primary.

## Testing
`python3 -m pytest ops/tests/test_wq2100_kb_append.py` — 3 passed. Not run against the live kb: the worker allowlist refuses invoking the script, so `kb-append wq2097-issue-log` is a follow-up for an operator shell.

## Risk / rollback
Touches only kb when invoked; new files only. Revert the commit to roll back.
