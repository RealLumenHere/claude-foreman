# wq#4657 verify of wq#4654 — CONTRADICT (judgment filed: wq#4706)

Verifier: orch-burndown-2 (orchestrator-side; fworker CAPABILITY_DENIED on ssh)
Date: 2026-10-26 PDT. Target: claude-foreman PR #2473, head 938dabc02b.

## The claim
wq#4654 closed done 2026-10-26 12:28 PDT: "conftest redirects test hook subprocesses off the real vault; action_gate.py gains prune_vault()/prune-vault CLI". Its own notes carry [UNMERGED-PR: PR #2473 open, awaiting review/merge].

## Evidence against
1. Gitea API pulls/2473: state=open, merged=false, mergeable=true. `git grep prune_vault origin/main -- harness` returns nothing; main's conftest has no vault-isolation fixture. The deliverable is not on main.
2. The defect is live and growing: ~/.secrets/gate-snapshots = 2440 files / 1.7G (ticket filed at 2415 / 1.6G).
3. Independent of (1): at the PR head nothing calls prune_vault/prune-vault outside action_gate.py and tests (git grep, no timer, no cron, nothing in /home/lumen/bin or systemd). Merged as-is it retires zero files.

## What held up
PR code is sound: harness/tests/test_action_gate.py 98 passed at 938dabc02b (PYTHONPATH=harness, detached worktree). Read-only dry run of prune_vault's predicate over the live vault: 2440 files, 1093 older than 30d, 517 of those carry a secret pattern and are correctly kept for secret-backup-sweep, 576 would be pruned. The conftest fixture is the right fix for the test-fixture leak.

## Class
Done-gate accepted an open PR as evidence (the [UNMERGED-PR] tag is advisory); and a retention function shipped without a scheduler. Both routed to wq#4706 (judgment, opus-5@high).

## Verdict
CONTRADICT. Ticket wq#4706 filed, deps 4657.
