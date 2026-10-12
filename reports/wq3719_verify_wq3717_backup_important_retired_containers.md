# wq#3719 verify: wq#3717 (backup-important red 4 days — retired-container ERRORS)

Archetype 8 adversarial verify pass. Subject: wq#3717 (claude-sonnet-5), PR #1747
merged as `c1341dc9` (and picked up into main's `85bf15db` merge). Verifier:
claude-sonnet-5@high (fresh instance, same model as author).

## What wq#3717 claimed

`backup-important.sh` exited 3 daily (10-08..10-11) because the dump legs for
`plausible_db`, `plausible_events_db`, `mongodb` — stopped by wq#3672's Phase-1
subtraction — kept running `docker exec` against them and counting the failure
as `ERRORS`, tripping `OnFailure`→ntfy daily with no real data loss (restic legs
kept succeeding). Fix: `RETIRED_CONTAINERS` list + `container_running` /
`container_is_retired` / `skip_or_error` helpers (SKIP, not ERROR, for a
not-running container on the list; still ERROR for any other unexpected stop).
Also: moved a plaintext MongoDB credential out of the script into a new 600
`.mongo-credentials` file, and made the restic stage path stable
(`$STAGE_DIR/important`, not `-$TS`) so restic finds its parent snapshot.
Claimed as edited live directly (lane's Edit/Write could reach
`/home/lumen/services/`, Bash could not) plus a staged copy for audit at
`ops/wq3717-a2-backup-important-fix/`. ISSUE-LOG row 1250.

## What I checked (tried to break, not re-derive)

- **Live file matches the staged/audited copy, byte for byte.** Read
  `/home/lumen/services/backup/scripts/backup-important.sh` and
  `/home/lumen/services/backup/scripts/env.sh` directly (outside this repo) and
  compared against `ops/wq3717-a2-backup-important-fix/{backup-important.sh,env.sh}`
  — identical. This matters: ISSUE-LOG rows 926b/940 record a prior incident
  (wq#2910/wq#2932/wq#2943) where a ticket closed "done" on a merged `ops/` bundle
  that was never actually deployed to the live path. That gap does not apply
  here — the live script genuinely carries the fix, not just the repo's audit copy.
- **Baseline reproduced from real evidence, not assumed.** Read
  `/home/lumen/services/backup/logs/important-20261011.log` (pre-fix, 10-11 run):
  `Error response from daemon: No such container: plausible_db` /
  `plausible_events_db` / `mongodb`, ending `=== DONE — errors=3 ===`, with the
  stage dir literally named `important-20261011_110700` (confirms the
  pre-fix timestamped-path restic problem too, "no parent snapshot found, will
  read all files" on both repos). This exactly matches the ticket's claimed
  symptom and gives a concrete control to reason against.
- **Logic trace of the fix against that baseline.** `container_running()` uses
  `docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null`, which returns
  non-`"true"` whether the container is merely stopped *or* fully removed (the
  10-11 log shows "No such container", i.e. removed, not paused) — so the fix is
  correct regardless of which state wq#3672's subtraction actually left them in,
  even though `retired_services.yaml`'s `important-tier-phase1-subtraction` entry
  says "confirmed stopped, not crashed" (arguably imprecise — the daemon says
  the containers don't exist at all — but irrelevant to this fix's correctness).
  Replaying the three call sites (`:113-126`, `:130-157`, `:161-177`) by hand
  against the 10-11 log: all three now log SKIP instead of FAILED, ERRORS stays
  at 0 from those legs, so the daily run would exit 0 (modulo the unrelated
  Langfuse/Gitea/VM legs, which were OK that day) instead of 3. No daily
  timer run has fired since the fix landed (03:02 UTC; the timer's observed
  run time is ~11:07 UTC, ~8h out) so I could not observe a post-fix exit code
  directly — this is a logic-trace confirmation against real historical
  evidence, not a live re-run.
  Scope check: any container stop *not* on `RETIRED_CONTAINERS` still hits the
  `else` branch of `skip_or_error` and still increments `ERRORS` — the fix
  doesn't silently widen into a "never alert on a stopped container" regression.
- **Credential move is real, not cosmetic.** `env.sh` (live, matches staged)
  defines `MONGO_CREDENTIALS_FILE=/home/lumen/services/backup/.mongo-credentials`
  with its own "DO NOT add secrets here" header intact; the script `source`s
  that file only inside the `mongodb`-is-running branch. Did not open
  `.mongo-credentials` itself (reading credential files is off-limits per house
  rules) but confirmed the reference path is correct and the old inline
  `--password=<literal>` string is gone from both the live and staged script.
- **The `dispatcher/config.json` 38/-7 line diff in the merge commit's stat is
  not part of wq#3717's actual change** — `git diff c9ae1773 c1341dc9 --
  dispatcher/config.json` shows it's the `cloud_ceiling 10→5` edit from
  `343bbf7f` (wq#3673, already on main before this branch merged), picked up by
  the merge. Checked so it isn't mistaken for scope creep.

## One latent gap found (not a contradiction of the ticket's claim)

`backup-important.sh` runs under `set -uo pipefail` (no `-e`). If `mongodb` is
ever un-retired (restarted) while `.mongo-credentials` is missing or unreadable,
`source "$MONGO_CREDENTIALS_FILE"` fails but does **not** stop the script (no
`-e`); the next line still expands `"$MONGO_BACKUP_USER"`/`"$MONGO_BACKUP_PASSWORD"`
for the `docker exec` argv, and under `set -u` that unset-parameter expansion
aborts the whole script immediately and unconditionally (the exemption bash
gives `-e` inside an `if` condition does not apply to `-u`) — before the `if`
is even evaluated. That would silently skip the two restic snapshots after it
(the actual backup), not just log "FAILED MongoDB dump (continuing)" as the
surrounding code implies. Currently dormant: `mongodb` is stopped today, so
`skip_or_error` is taken and this branch never executes. Flagging per the
acceptance-probe doctrine (wq#2630) — the fix was only exercised on the
retired/stopped path, not the "credentials file missing while the service is
live again" path outside that default. Does not contradict wq#3717's claim
(today's alarm-fatigue bug is fixed and deployed); recommend a follow-up ticket
to add `|| { log "FAILED MongoDB dump (continuing)"; ERRORS=$((ERRORS+1)); }`-
style guarding around the `source`, not a reopen of wq#3717.

## Verdict

**CONFIRM.** wq#3717's fix is deployed live (verified byte-identical to the
audited copy), its control-flow logic correctly eliminates the three
retired-container ERRORS against the actual 10-11 failure log, keeps real
unexpected-stop detection intact, and the credential/stage-path changes are
real and correctly wired. One low-severity latent robustness gap noted above
for follow-up, not a contradiction — no escalation to opus-5@high.
