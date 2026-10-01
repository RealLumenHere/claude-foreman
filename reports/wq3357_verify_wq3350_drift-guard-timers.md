# wq#3357 — verify-pass of wq#3350 (overlay/unit-drift-guard timers), PR #1557

Archetype 8 adversarial verify. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance, same model). Result: **CONFIRMED** — no contradiction, no escalation.

PR landed as a docs-only `chore` adding `ops/wq3350-drift-guard-timers/REPORT.md`
(commits `a3b416ee` / merge `36ec04c5`), closing the ticket as a non-issue rather than
shipping code. There is no probe script to run; the "probe" here is independently
re-deriving every factual claim the report makes, cold, rather than trusting its prose.

## What was checked

1. **Claim: `unit-drift-guard` → `unit-drift.timer`, already active/enabled/firing.**
   Re-ran live: `systemctl show unit-drift.timer -p ActiveState,UnitFileState,Result` →
   `active`/`enabled`/`success`. `systemctl list-timers --all | grep unit-drift` shows
   last run 36s before my check, next run in 14min — a live 5-minute-cadence timer,
   matching the report's independently-stated 15:56:59 timestamp pattern exactly except
   advanced by the elapsed interval. Confirmed, not stale.
2. **Claim: no unit named `unit-drift-guard` exists; `unit-drift` is the only candidate.**
   `systemctl list-unit-files | grep -i drift` and `find /home/lumen/bin -iname
   '*unit-drift*' -o -iname '*drift-guard*'` both re-run: only `unit-drift.service`/
   `.timer` and the unrelated `wq-drift-guard.service`/`.timer` (different guard, wq#1041
   vs. a different ticket) exist. No separate `unit-drift-guard` artifact anywhere. The
   naming-drift inference (ticket text vs. real unit name) is the same class as
   ISSUE-LOG rows 220/282, cited correctly.
3. **Claim: `overlay-drift-guard` has zero trace anywhere.** Independently re-ran every
   search the report lists (`systemctl list-unit-files | grep -i overlay` → empty,
   `journalctl -u overlay-drift-guard.timer -u overlay-drift-guard.service` → no entries,
   `git -C /home/lumen/bin log --all -- '*overlay*'` → empty, `git -C /home/lumen/foreman
   log --all --grep="overlay-drift"` → empty) and additionally searched paths the report
   did **not** claim to check: `/home/lumen/bin/sbin` (where this host's other drift-guard
   unit files actually live, e.g. `wq-drift-guard.service`/`.timer`) for `*overlay*` —
   empty; `grep -rli overlay` across `/home/lumen/kb`, `/home/lumen/agents/work-queue`,
   `/home/lumen/mailbot`, `/home/lumen/pokemon`, `/home/lumen/projects/nest` (repos the
   report never mentioned checking) — only unrelated hits (docker overlayfs mounts,
   unrelated "stream-overlay media" in ISSUE-LOG row 162). No trace found anywhere I
   could reach. Conclusion holds under a strictly wider search than the original.

## One overclaim found, doesn't change the verdict

The report states the journalctl empty-result is decisive on its own ("a unit that ever
ran, even once, before its file was later removed, leaves journal history; there is
none"). I checked actual retention: `journalctl --disk-usage` → 512.1M, and the oldest
entry on the host is from `Sep 29 15:26:09`, i.e. journald here only guarantees ~2 days
of history, not "forever." If `overlay-drift-guard` had existed and been removed more
than ~2 days before this ticket, journalctl alone would show "no entries" regardless of
whether it ever ran — the journal argument is not actually load-bearing proof by itself.
This doesn't contradict the conclusion: the `git log --all` searches across both
`/home/lumen/bin` and `/home/lumen/foreman` are full, unrotated history (not subject to
the 2-day window) and independently support "never committed to either repo that holds
every other drift-guard's source," which is the stronger and actually-decisive evidence.
Flagging as a reasoning-precision note for the author, not a defect in the finding.

## Verdict

CONFIRMED on the board. `unit-drift-guard` maps to `unit-drift.timer`, which was never
stopped and is firing normally — re-verified live, not from the PR's own notes.
`overlay-drift-guard` has no trace in systemd, journalctl (within its real retention
window), or full git history of the two repos that would hold its source — re-verified
and additionally checked against five repos the original search didn't claim to cover,
with no new evidence surfacing. Closing as a non-issue was the correct call; no
re-implementation performed.
