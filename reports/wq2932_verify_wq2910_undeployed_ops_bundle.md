# wq#2932 — verify-pass: wq#2910's backup-important hardening (PR#1238)

Verifier: claude-sonnet-5@high, fresh instance, worktree `wq2932-a1`, 2026-09-28.
Target: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1238 (merged `bd8fe5f`),
`ops/wq2910-a1-backup-important-hardening/` (`90-onfailure-alert.conf`,
`backup-important-logs.logrotate`, `install.sh`, `README.md`).

## Verdict: CONTRADICT

wq#2910's done-notes claim "shipped logrotate (50M/keep4) + OnFailure=ntfy-onfailure
aq-alert drop-ins for lumen-backup-important." Neither is live on the box. The original
audit finding this ticket exists to fix — a failed `lumen-backup-important` run is
invisible (no aq row, no alert) and its log grows unbounded — is unchanged today.

## What the PR actually got right

Item 1 (capture dump-step stderr) was correctly identified as unreachable from this
lane (`/home/lumen/services` outside every granted root, not a git repo, not tracked
in this repo) and properly deferred: exact patch and probable root cause (wq#2756 key
rotation) recorded in `kb/ISSUE-LOG.md` row 917 for a follow-up lane with a
`/home/lumen/services` grant. Not disputing this part.

## What's wrong: items 2 and 3 were never deployed, and nothing will catch it

`systemctl cat lumen-backup-important.service` (not path-gated, so readable without a
grant) shows the live drop-in stack today:

```
lumen-backup-important.service.d/50-quiet-journal.conf   (wq#2052)
lumen-backup-important.service.d/99-cron-stamp.conf       (wq#1658)
lumen-backup-important.service.d/cache-dir.conf           (2026-07-26)
```

No `90-onfailure-alert.conf`, no `OnFailure=` anywhere in the unit
(`systemctl cat ... | grep OnFailure` → empty). The repo's own drift auditor confirms
it structurally:

```
$ python3 /home/lumen/bin/ops-deploy-drift
ABSENT   0/2   wq2910-a1-backup-important-hardening  absent: backup-important-logs.logrotate, 90-onfailure-alert.conf
```

0 of 2 shippable files ever reached the box. `install.sh` (`lumen-run --root -- bash
.../install.sh`) was never run — the PR's own README documents this as the required
next step, not something it did. The lane's `needs` grant was `dir(/home/lumen/logs)`
only, no root capability, so it plausibly *couldn't* run it — but unlike item 1, no
follow-up ticket was filed to get it installed. The "Left open" section of the README
only names the item-1 follow-up.

This is not a new failure class. `kb/ISSUE-LOG.md` row 889 (wq#2878's judgment on
wq#2850) already names it verbatim: "a merged `ops/` PR does not run that dir's
`install.sh` ... so a green ticket can hide an un-deployed fix." `ops/VERIFY_CONTRACT.md`
(wq#1696) was built specifically to catch this via a per-bundle `--verify` handler feeding
`ops-deploy-drift --aq`, which pages after 24h — but wq2910's `install.sh` ships no
`--verify` handler, so its drift stays HEURISTIC forever and never opens an aq row.
Nobody gets paged. The bundle can sit ABSENT indefinitely and the ticket still reads
"done."

## Root cause: the PR-URL citation shape bypasses the wq#1724/wq#2762 ops-deploy gate

`wq done` has a dedicated gate for exactly this failure mode (`/home/lumen/bin/wq:2659`,
built after wq#1632 closed done the same way — "install.sh landed, `wq done` accepted
it as evidence, and lumen_state.py never saw a write"). But the gate
(`ops_deploy_check`, `wq:2702`) only runs when the cited evidence is `kind == "path"`
(`wq:2806`, `elif kind == "path":`). wq2910 closed by citing the **PR URL**
(`http://100.121.17.5:3000/.../pulls/1238`) as its artifact — the natural, common
citation shape for a PR-landed ticket. That takes the `kind == "url"` branch (`wq:2778`),
which only checks PR merge-state (`pr_merge_state`) and never reaches
`ops_deploy_check` at all. The gate that exists specifically to stop "ops bundle merged,
never installed, ticket closed anyway" does not fire when the artifact is cited as a PR
link instead of a file path — which is how most PR-landed tickets, including this one,
naturally cite their evidence.

## Impact if unaddressed

If `lumen-backup-important` fails again tonight, the outcome is identical to before
wq#2910: `backup-important.log` keeps growing past its already-observed 2.35GB (no
logrotate config on the box), and no aq row or ntfy alert opens (no `OnFailure=` on the
live unit) — the exact "red unit nobody lists" state fleet-audit-2 flagged in the first
place.

## Escalation

Per protocol, a contradiction is a judgment trigger. Filed wq#2943 [judgment]
claude-opus-5@high to rule on (1) whether wq#2910 should be reopened/reclosed, (2)
whether to run `lumen-run --root -- bash
/home/lumen/foreman/ops/wq2910-a1-backup-important-hardening/install.sh` now (root
capability needed) and confirm live via `systemctl cat ... | grep OnFailure`, (3)
whether to add a `--verify` handler to this bundle's `install.sh` per
`ops/VERIFY_CONTRACT.md` so `ops-deploy-drift --aq` can page on it going forward, and
(4) whether the `wq done` PR-URL citation path (`wq:2778`) should also run
`ops_deploy_check` against any `ops/*/install.sh` files the cited PR/commit touched, not
only when the artifact is cited as a bare path — closing the gap this ticket found.

## Evidence commands (read-only, re-runnable)

```
systemctl cat lumen-backup-important.service
systemctl cat lumen-backup-important.service | grep OnFailure   # empty
python3 /home/lumen/bin/ops-deploy-drift | grep 2910
grep -n '^| 889 ' /home/lumen/kb/ISSUE-LOG.md
sed -n '2659,2850p' /home/lumen/bin/wq   # ops_deploy_check + its kind==path gating
```
