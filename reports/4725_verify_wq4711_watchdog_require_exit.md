# wq#4725 — verify-pass of wq#4711 (watchdog `--require-exit`)

**Verdict: CONFIRM.** The shipped fix closes the bug as claimed, is correctly
tested, and does not regress plain `-a`. One narrow, out-of-scope gap found
(marker path) and one already-disclosed deployment nuance (primary-tree
fast-forward pending) are noted below; neither contradicts the ticket's claim.

Author: claude-sonnet-5 (wq#4711, lumen-bin PR #279, merged `5e84ad41e4` /
local-main `98c3188`). Verifier: claude-sonnet-5@high, fresh instance, wq#4725.

## What was claimed

wq#4711: plain `watchdog -a ARTIFACT` declares `OK` as soon as the artifact's
size is stable for `--stable` polls, without waiting for the producer's real
exit code — wrong for build jobs where a post-artifact stage (e.g. stage11b/
stage12 after `xorriso`) can still fail. Fix: `--require-exit` — once stable,
keep polling instead of guessing; the only path to a verdict becomes the
pre-existing "process finished" branch (exit 0 + artifact present → `OK`,
nonzero → `FAIL`). Ticket's own failure case: artifact written, producer
sleeps ~70s, then exits 1 → must be `FAIL`, not `OK`.

## Diff read (cold)

Read the merged diff directly (`git -C /home/lumen/bin show main:watchdog`,
since `/home/lumen/bin`'s primary checkout is on an unrelated branch — see
"Deployment nuance" below). The change is minimal and localized:

- `REQUIRE_EXIT=0` default, `--require-exit` sets it, validated to require
  `-a`/`--artifact` (dies otherwise — confirmed by the ticket's own test).
- At the one place the artifact-stability branch used to `break` straight to
  a grace-wait-then-guess verdict, it now checks `REQUIRE_EXIT`: if set, it
  logs and falls through without breaking, so the loop's branch 1 (`RCF`
  populated → real exit code) is the only remaining path to `OK`/`FAIL`.
  Plain `-a` (no `--require-exit`) is untouched — same grace-wait/guess
  behavior as before.
- Branch 1 (exit-code handling), branch 3 (`--marker`), branch 4 (`--timeout`
  kill), branch 5 (vanished-without-rc) are all pre-existing and unedited by
  this diff.

This matches the ticket's description exactly: minimal footprint, additive
flag, no change to default behavior.

## Verification run (author's tests, from scratch)

Did **not** reuse the author's checkout — pulled `tests/test_wq4711_watchdog_require_exit.py`
and `watchdog` from `main` (`git -C /home/lumen/bin show main:<path>`) into a
clean scratch dir and ran them cold:

```
tests/test_wq4711_watchdog_require_exit.py::test_require_exit_waits_past_artifact_stability_and_reports_fail PASSED
tests/test_wq4711_watchdog_require_exit.py::test_require_exit_reports_ok_only_after_clean_exit PASSED
tests/test_wq4711_watchdog_require_exit.py::test_plain_artifact_flag_unchanged_without_require_exit PASSED
tests/test_wq4711_watchdog_require_exit.py::test_require_exit_rejected_without_artifact PASSED
4 passed in 7.37s
```

## Independent adversarial probes (not the author's test file)

The author's failure-mode test compresses the 70s field repro to a 4s sleep.
To check the fix holds outside that compressed shape, built a fresh A/B probe
with a 40s post-artifact delay (long enough to exceed plain `-a`'s fixed
~30s grace window — the actual trigger condition for the bug; a first attempt
at 8s sleep did **not** trigger the baseline bug, confirming the probe's
input matters and isn't trivially satisfied):

- **Without `--require-exit`** (same producer: write artifact, sleep 40,
  exit 1): watchdog reported `OK` at `duration_s=32` — `"artifact ready (1
  bytes) after 2s, producer still running pid=1922936"`. Confirmed that PID
  was still alive (`ps -p 1922936`, etime 00:39) at the moment `OK` was
  already on disk — this is the exact bug wq#4711 describes, reproduced
  live, independent of the author's test.
- **With `--require-exit`**, same producer: watchdog correctly waited and
  reported `FAIL`, `rc=1`, `"exit 1 after 41s"`, `duration_s=41`.

Clean A/B on the same producer shape: fix closes the premature-`OK` window
the unfixed path exhibits.

## Findings

### 1. `--require-exit` does not extend to the `--marker` completion path (non-blocking)

Branch 3 (`-m/--marker` log-match) is unconditional — it fires regardless of
`REQUIRE_EXIT`. Probed: `-a ARTIFACT --require-exit -m DONE_MARKER` with a
producer that writes the artifact, prints the marker, then sleeps and exits
1 → watchdog reports `OK` (`"log matched /DONE_MARKER/ after 2s, producer
still running pid=..."`) while the producer is still running and will later
fail — the same bug class the ticket fixes, via a different trigger.

This is **not a contradiction** of wq#4711's claim: the flag's own doc text
and the ticket title scope `--require-exit` to the artifact-stability path
only ("with -a: artifact stability alone is not OK"), and `grep` across
`foreman`/`bin` found no existing watchdog invocation that combines `-m` with
`-a` for build jobs — so this isn't live in production today. Flagging as a
documentation/follow-up note: if `--require-exit` is ever combined with
`-m`, it does not (yet) guard that path.

### 2. Deployment nuance: primary `/home/lumen/bin` tree not yet fast-forwarded (already disclosed, not a defect)

Ran the ticket's own liveness probe against the live file:
`grep -q -- '--require-exit' /home/lumen/bin/watchdog` → exit 1 (not found).
`/home/lumen/bin`'s primary checkout is on `fix/wq4720-unmerged-followup`
(another lane's in-flight branch), not `main` — confirmed via `git -C
/home/lumen/bin rev-parse --abbrev-ref HEAD`. Reading the stale script's own
arg parser (`-*) die "unknown option $1 (see --help)"`) means a live
`/home/lumen/bin/watchdog --require-exit` invocation right now would hard-fail
with "unknown option --require-exit", not silently misbehave.

This is explicitly disclosed by the author in
`wq4711_cross_repo_evidence.md:17-33` as the expected shared-primary-tree
pattern used by this fleet (cites wq#3487/wq#192/wq#2569/wq#2573/wq#3315 as
precedent) — it resolves once the concurrent wq4720 lane finishes and
`lumen-pr ffprimary /home/lumen/bin` runs. The code is genuinely merged to
`main` and correct; it just isn't live on the shared primary checkout yet.
Not a wq#4711 defect — noting it here only because it means a human or
automation reading `/home/lumen/bin/watchdog` directly right now would see
the bug as still "live," which could be confusing without this context.

### 3. `fleet_tools.yaml` / wq#4635 doc-default update — already deferred to wq#4724

Ticket also asked to make `--require-exit` the documented default for build
jobs in `fleet_tools.yaml`. Author's notes and `wq4711_cross_repo_evidence.md:95-107`
disclose this wasn't done (no `minerva` repo access this session) and file
it as follow-up wq#4724. Confirmed via `/home/lumen/bin/wq show 4711` notes.
Not re-litigating scope here — already tracked.

## Conclusion

wq#4711's core fix (the thing the ticket's acceptance criteria and liveness
probe test — artifact-stability no longer reporting `OK` before the real
exit code, for the `-a`/`--require-exit` path) is correct, tested by the
author, and independently reproduced/confirmed here with a fresh A/B probe.
No regression to plain `-a`. **CONFIRM** — no escalation.
