# PILOT 84f045fbb1ce — Report

Runner: wq#3432, worktree wq3432-a2. Attempted 2026-10-03.

## Outcome: baseline does not reproduce (kill criterion hit)

Per CARD.md "Kill criteria": *"Baseline does not reproduce -> stop, report the
baseline defect. Do not proceed."* That condition fired before the first command:

1. **No path to the run node.** CARD.md pins `Node: hathor`, but this worker is
   sandboxed to a fixed worktree plus a short `--add-dir` list
   (`/home/lumen/agents/work-queue`, `/home/lumen/mailbot`, `/home/lumen/pokemon`,
   `/home/lumen/bin`, `/home/lumen/foreman`, `/home/lumen/kb`, `/tmp/fworker-wq3432`).
   `ssh` has no entry in the per-command allowlist (`grep -n "ssh"
   /home/lumen/foreman/harness/worker-settings.json` returns nothing), so there is no
   way for this session to execute anything on hathor. `fleet-exec`'s own policy
   (`/home/lumen/foreman/harness/fleet-exec-policy.yaml:183,195-211`) does grant
   `ssh: [... hathor]`, but only for a narrow `run_readonly_cmds` probe set (`true`,
   `uptime`, `hostname`, `uname`, `cat`, `ls`, `stat`, `df`, `free`, `systemctl
   status|...`, `journalctl`, `ss`, `ip addr`, `tailscale status`, `docker ps`,
   `docker logs`) plus one pinned python probe
   (`fleet-exec-policy.yaml:217-218`, disk-usage only). There is no verb that runs an
   arbitrary script (`run_verify.py`, `eval_ndcg.py`) on hathor — `copy` only lands
   files under hathor's `pilots`/`lumen-drop` dirs (`fleet-exec-policy.yaml:243`), and
   `peer` is empty by design (`fleet-exec-policy.yaml:274`). So even routing through
   fleet-exec instead of raw ssh cannot execute the card's baseline or treatment.
2. **Fixed sample is unreachable.** The card's sample path
   `/mnt/data/lumen/verify_set/verify_rows.jsonl` is outside every allowed directory
   for this session; a direct `ls` on `/mnt/data/lumen/verify_set/` is refused
   ("may only list files in the allowed working directories").
3. **The harness scripts are likewise unreachable.** `/mnt/data/lumen/harness/
   run_verify.py` and `eval_ndcg.py` sit under the same unreachable `/mnt/data/lumen`
   root, so even a copy-based workaround would need read access this session does
   not have.

No baseline queries were executed; `baseline_scores.jsonl` / `baseline_ndcg.json`
were never produced, so nDCG@10 was never measured on either arm.

## Precedent

This is the same structural blocker hit and recorded by two prior pilot runs in this
repo: `pilots/37810e18b41e/REPORT.md` (wq#3408, hathor unreachable — no `ssh` on the
worker allowlist) and `pilots/18d66c25806e/REPORT.md` (wq#3139, card paths absent).
Consistent with those precedents, this is reported as a kill-criterion SKIP/WATCH, not
invented around: `research/DEFER_PILOT_LANE.md` §3.1a reserves authoring a
measurement plan for the card's generator, not the runner, and building a substitute
sample/harness from scratch here would be exactly that.

## Why this isn't a research-prompt rescue

The open question (does CLM-v0.1-8B beat lumen-rerank + SpanUQ-lite on Lumen's verify
set) is still unanswered and still requires the real run the card describes. This
report does not attempt to answer it from memory or by building a different test.

## What would unblock a re-run

- Grant the dispatched worker either `ssh`-to-hathor on the per-command allowlist, or
  a fleet-exec verb that can execute an arbitrary pinned script on hathor (today's
  `run` verb is read-only-probe-only).
- Re-home the card's fixed-sample and harness paths (`/mnt/data/lumen/...`) under a
  root the worker already has (`/home/lumen/foreman`, or a hathor `pilots`/
  `lumen-drop` dir reachable via `copy`), or add `/mnt/data/lumen` to the worker's
  `--add-dir` grant for pilot tickets.

RECOMMENDATION: SKIP
Measured numbers: none — baseline unreproducible (0 rows scored on either arm);
nDCG@10 delta not measured.
