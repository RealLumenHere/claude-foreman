# wq#2969 — verify-pass of wq#2953 (PR #1264): CONFIRMED

Archetype 8 adversarial verify of `reports/wq2953_memoryd_restart_verified.md`
(shipped diff: that single 69-line file, docs-only, no code changed). Verifier
claude-sonnet-5@high, fresh instance, same model as author.

## What the PR claims

At dispatch, `lumen-memoryd` was serving a stale/inconsistent contract and
fworker `/commit` calls were 403ing as "not in the wq session registry". By
the time wq#2953 was worked, some other actor had already restarted the unit;
the report claims the restart landed a correct contract (`lane_drift=[]`) and
that fworker-identity registry rejects went from 5630 (pre-09:10:00Z) to 0
(post-09:10:44Z). It also corrects ISSUE-LOG row 932: `lumen-run start --root
-- systemctl restart <unit>` (wq#1350) is a working, already-allow-listed
restart path, so row 932's "cannot restart" conclusion was wrong.

## What I did to try to break it

Ran all three of the report's own evidence queries again, live, ~15-20
minutes after the report was written (2026-09-28, report ~09:14Z, my checks
~09:28-09:30Z), plus extra checks the report didn't run:

1. `systemctl show lumen-memoryd -p ActiveEnterTimestamp -p MainPID
   -p ActiveState -p NRestarts` → **the unit had restarted again** since the
   report: `ActiveEnterTimestamp=09:26:25 UTC`, PID 3918949 (report cited
   09:12:07 UTC / PID 3545850). `journalctl -u lumen-memoryd --since 09:26:00`
   shows this was a deliberate `systemctl kill` cycle by some other actor
   (`Sent signal SIGKILL to main process ... on client request`), not a
   crash — unrelated churn from concurrent fleet activity on the same unit
   today (see `main` git log: wq#2952/wq#2965 also touch memoryd this
   session). This is new information not in the report, but it does not
   contradict the report's claim, which was scoped to the state as observed
   at the time.

2. `curl -s http://127.0.0.1:8139/healthz` **after** that second restart
   still returns `contract_sha256: afba29380f741660154b867f3a114109baca5d21fa17ce2c5b3a05d1a62c5cce`
   (byte-identical to the hash the report cites) and `lane_drift: []`. The
   wq2901 contract fix is still loaded post-restart — the fix is not
   restart-fragile.

3. Read-only `memory_audit` query (`file:/home/lumen/minerva/minerva.db?mode=ro`),
   re-run and extended past what the report checked:
   - Pre-09:10:00Z fworker-identity registry rejects: **5630** — exact match
     to the report.
   - `mv4-w575-05322f` churn-window rejects (09:11:35-09:12:13Z): **90** —
     exact match to the report's "90 rows, zero since" note.
   - fworker-identity registry rejects since 09:10:44Z, re-checked as of
     09:30Z (20 min further out than the report's own check): **0**.
   - New finding, absent from the report: identity `not-a-registered-session`
     hit the same reject message 3 more times post-cutoff (09:11:35, 09:16:55,
     09:25:56Z). Checked its full history: only 5 occurrences total, oldest
     2026-08-15 — a long-lived, low-frequency, non-`fworker-*` identity
     (reads as a negative-path health probe, not a real caller), present
     before wq#2901/wq#2953 and unaffected by them. Doesn't fall inside the
     report's stated scope ("fleet-worker memory_audit rejects... have
     stopped") and doesn't contradict it.
   - Spot-checked the 5 lanes the report names as "confirmed real writes":
     `fworker-wq2913`, `fworker-wq2943`, `fworker-wq2952`, `fworker-wq2955`
     (1 write each), `fworker-wq2962` (3 writes) — all `action='write'`,
     zero registry rejects for any of them since 09:10:44Z. Matches.

4. Confirmed the PR's actual shipped diff: fetched `refs/pull/1264/head`
   (`fa67d51`) directly from `origin` and diffed it against foreman `main`
   HEAD (`0612447`) — `git diff FETCH_HEAD 0612447 -- reports/wq2953_memoryd_restart_verified.md`
   is empty. The PR is exactly the report already on `main`; nothing else
   changed underneath it.

## Verdict: CONFIRMED, no contradiction

Every checkable number in the report reproduces exactly against live state,
run independently and later in time than the author's own checks, including
through an intervening unrelated restart of the same daemon. The one gap I
found (`not-a-registered-session`) is pre-existing, out of the report's
stated scope, and doesn't change the acceptance conclusion. No escalation.

## Evidence queries (reproducible)

Same three as the original report, plus the fworker-pattern filter and the
`not-a-registered-session` full-history check:

```
systemctl show lumen-memoryd -p ActiveEnterTimestamp -p MainPID -p ActiveState -p NRestarts
curl -s http://127.0.0.1:8139/healthz
python3 -c "import sqlite3; c=sqlite3.connect('file:/home/lumen/minerva/minerva.db?mode=ro', uri=True); ..."
  # memory_audit, detail LIKE '%not in the wq session registry%', split by ts
  # vs '2026-09-28T09:10:44Z', further split by detail LIKE '%fworker%'
git -C <worktree> fetch origin refs/pull/1264/head
git -C <worktree> diff FETCH_HEAD <main-HEAD> -- reports/wq2953_memoryd_restart_verified.md
```
