# wq#4632 — Judgment pass (archetype 8, opus-5@high escalation): wq#4629 Commander watchdog scripts

**Verdict: CONTRADICT.** wq#4629's shipped watchdog bundle
(`ops/wq4629-commander-watchdog-a1/`, PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2459,
merged `a8d6b1fa`) does not deliver the one thing it exists to deliver: surviving a job
that outlives a single poll window. `lumen-job-notify.sh` dies silently on the **first
real** timeout instead of looping past it, because of a `set -e` / command-substitution
interaction the shipped test cannot catch (its stub never reproduces the real exit code).
sonnet-5@high (me) found and reproduced this against the real `/home/lumen/bin/lumen-run`
binary; escalated per protocol; opus-5@high independently reproduced it from scratch
(own harness, own stub, own run against the real binary) and found a second, more severe
bug in the README's launch recipe. Both verifiers agree: CONTRADICT.

Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance). Escalated
to opus-5@high on contradiction, per ticket protocol.

## What wq#4629 claims

Research doc `research/wq4629_commander_watchdog_survival.md:192-196` states
`lumen-job-notify.sh` "Loops `lumen-run wait UNIT` past `TIMEOUT` results until a terminal
state, then writes a completion marker JSON," and §7 ("Evidence") cites
`pytest ops/wq4629-commander-watchdog-a1/test_commander_watchdog.py` → "2 passed" as proof
this re-poll-past-timeout behavior works end to end. The whole point of the bundle (per
the parent ticket's own framing — `build-limpet.sh` takes 30+ minutes, Commander's call
window is ~70s) is bridging a job that takes far longer than any single poll call.

## What I found

`lumen-job-notify.sh:10` sets `set -euo pipefail`. The poll loop
(`lumen-job-notify.sh:37-44`):

```bash
result=""
while :; do
  out=$(LUMEN_RUN_WAIT_CAP="$poll_cap" "$lumen_run" wait "$unit" "$poll_cap" 2>&1)
  case "$out" in
    *TIMEOUT*) continue;;
    *) result="$out"; break;;
  esac
done
```

Bash's `set -e` exemption for a failing command substitution applies only when that
substitution is the direct condition of `if`/`while`/`until`, negated with `!`, or on the
non-last leg of `&&`/`||`. A **plain assignment** (`out=$(cmd)`) is none of those: if `cmd`
exits non-zero, the assignment's own exit status is non-zero and `set -e` kills the script
immediately — the `case` statement below it never runs.

The real `/home/lumen/bin/lumen-run` (`lumen-run:139-166`, read directly, not inferred) does
exactly this on timeout: `echo "TIMEOUT after ${budget}s (still RUNNING)"` then `exit 124`
(`lumen-run:156-160`). So the very first time a job genuinely outlives one poll window — the
scenario this script was built for — `lumen-run wait` returns 124, the assignment kills
`lumen-job-notify.sh` on the spot, and the re-poll `case`/`continue` never executes.

The shipped test's stub (`test_commander_watchdog.py:21-37`) does not reproduce this: its
`TIMEOUT` branch is `echo "TIMEOUT after 1s (still RUNNING)"` with **no `exit`**, so the stub
returns 0 — a value the real binary never returns on timeout. `test_lumen_job_notify_repolls_past_timeout_and_writes_marker`
therefore only validates a code path the production binary cannot produce, and "2 passed"
gives false confidence about exactly the resilience property the test's own name claims to
cover.

## Evidence (mine)

Built `/tmp/fworker-wq4632/fake-lumen-run`, a stub that mirrors `lumen-run wait`'s real
contract faithfully: `echo "TIMEOUT after 1s (still RUNNING)"; exit 124` on the first call,
`echo "DONE:0"; exit 0` on the second (i.e. identical to the shipped test's stub, **plus**
the one line — `exit 124` — the real binary has and the stub omits). Ran the actual shipped
`lumen-job-notify.sh` (not a rewritten copy) against it with `--poll-cap 1`:

```
returncode: 124
stdout: ''
stderr: ''
marker exists: False
```

One call, no re-poll, no marker, no error surfaced anywhere — silent death, exactly as
predicted from reading `set -e` semantics. Isolated the mechanism further in a minimal
repro (`/tmp/fworker-wq4632/settest.sh`): a `while` loop around `out=$(f)` where `f` echoes
then `return 124` exits the whole script on iteration 1 with no output at all, confirming
this is a `set -e` issue, not something specific to `lumen-run` or this script's structure.

## Escalation: opus-5@high independent reproduction

Spawned a fresh opus-5@high instance, given only the hypothesis and told to verify or
refute it from scratch with its own harness (`/tmp/opus-wq4632-judge/`, independent of
mine). Findings:

- **Confirmed the `set -e` mechanism** with 6 isolated cases (plain assignment, env-prefixed
  command, inside a loop body, `set -o posix` — all die; `local out=$(...)` and
  `out=$(...) || rc=$?` survive, neither of which the shipped script uses).
- **Confirmed against the real `/home/lumen/bin/lumen-run`, no stub at all**: pointed
  `LUMEN_RUN_WAIT_CAP=1 lumen-run wait systemd-journald.service 1` at a real always-active
  unit, got `(124, 'TIMEOUT after 1s (still RUNNING)')`, then ran the shipped
  `lumen-job-notify.sh` against that live binary: `rc=124`, no stdout, no marker — full
  end-to-end reproduction against production code, no stub anywhere in that run.
- **Found an additional, more severe bug**: the bundle's own README documents launching via
  `UNIT=$(lumen-run start ...)`. But `lumen-run start` prints the literal line `UNIT=<name>`
  (`lumen-run:114`), so `$UNIT` ends up holding `"UNIT=lumen-job-..."`, not the bare name.
  `lumen-run wait 'UNIT=lumen-job-x-1' 5` returns `(0, 'FAILED:?')` immediately (unknown-unit
  path), so the documented launch recipe sends a false FAILED:? marker within ~0.1s of
  starting *every* job, long or short — strictly worse than the TIMEOUT bug, which only bites
  jobs that outlive `poll_cap`.
- Checked for upstream safety nets: none exist. `commander-job-notify.path`/`.service` only
  watch/drain the done-dir; nothing supervises or restarts a dead `lumen-job-notify.sh`. The
  `LUMEN_JOB_EXIT=124` sentinel lands in the *notifier's own* transient unit's journal (if
  launched via `lumen-run start` per the README) and nothing ever reads it — `--collect`
  cleans the unit up. Confirmed via `systemctl list-unit-files 'commander-job-notify*'`
  (no match) that the bundle is not currently deployed, so there is no live incident, only a
  pre-deployment defect.
- Severity framing: default `poll_cap=3600` (1h) means a single `lumen-run wait` call blocks
  internally (via its own 3s-sleep loop) up to an hour before returning TIMEOUT, so jobs
  finishing within ~1h of the notifier's own start would not hit the TIMEOUT death in
  practice — only the independently-fatal README UNIT= bug, which hits every job regardless
  of duration. Jobs that genuinely need the re-poll loop (>1h, or any caller that passes a
  smaller `--poll-cap`) get silent, unmarked death. Net effect: the retry loop this script
  was written around is dead code in current form.

opus-5@high's full report, verbatim verdict: "Confirmed — `lumen-job-notify.sh` exits 124
silently with no marker on the first real `lumen-run wait` TIMEOUT (reproduced against the
real binary). Separately, the README's `UNIT=$(lumen-run start …)` recipe sends a false
`FAILED:?` marker instantly for every job."

## Disposition

- wq#4629: does **not** stand as shipped for the watchdog-script artifact (the research
  write-up's root-cause analysis and survey of prior art are accurate and not in question —
  only the two new scripts' correctness). Noted on the ticket.
- ISSUE-LOG row 1579: this finding, status "open (follow-up ticket pending)".
- Follow-up ticket **wq#4637** filed (lane build, pri 2, archetype mechanical-edit,
  deps=4629): rewrite the poll loop to capture `rc` via `|| rc=$?` and retry only on 124
  (writing an `ERROR:` marker on any other unexpected exit instead of dying silently); fix
  the test's `lumen-run` stub to `exit 124` on its TIMEOUT branch; fix the README's launch
  recipe to strip the `UNIT=` prefix.
- Not deployed (`commander-job-notify*` has no systemd unit file present), so no live
  incident — this is pre-deployment hardening, not an active outage.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
