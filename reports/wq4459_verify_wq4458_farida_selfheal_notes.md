# wq#4459 verify-pass of wq#4458 (PR claude-foreman#2337, merge 354ed8d2, branch commit d74acc6d)
Verifier: patch-s-18583b (claude-sonnet-5-5 @high), 2026-10-21 PDT. Cold read, no re-implementation.

## Verdict: CONTRADICT on liveness. The diff is sound; the live target does not have it.

## Confirmed (repo side)
- ops/wq3354-fleet-selfheal/selfheal.yaml parses as YAML; nodes.notes.farida present; farida is NOT in nodes.ignore or nodes.sleepers (correct per the ticket).
- Claim "no power facts in lumen_state": `lumen_state.py current farida` returns only hardware (M70q-Gen5) and role (limpet-reference-hardware). No availability/power predicate. TRUE.
- Farida is genuinely dark right now, not napping: tailscale status shows offline, last seen ~12h ago; ping 100.85.126.30 fails. Keeping it alerting is consistent with the note.

## CONTRADICTED (live side)
Ticket liveness: ops_verified(wq3354-fleet-selfheal). Ran `bash ops/wq3354-fleet-selfheal/install.sh --verify` (read-only):
    MISS seed key not live: nodes.notes.isis
    MISS seed key not live: nodes.notes.khepri
    MISS seed key not live: nodes.notes.farida
    MISS seed key not live: nodes.notes.apep
    VERIFY FAIL (4)
So wq#4458 was closed done on a repo merge only. The page for farida (and isis/khepri/apep) will NOT carry the context in the live sweep.
Mechanism: install.sh never overwrites live /home/lumen/agents/fleet-selfheal/selfheal.yaml (operator-owned); selfheal_merge.py (wq#4445) merges NEW seed keys in,
but is only invoked from the full root install path (install.sh line ~76), which has not been re-run since these seeds were added. The wq#4445 class fix added the
verify that detects this; it did not apply the merge.

## Why I did not apply it
Full install.sh = root rollout (units, daemon-reload, timers) and is gated by wq#2443. Running selfheal_merge.py alone against the live operator config is a
narrower, backed-up, notes-only change, but this is a verify ticket (verify, do not re-implement) and a contradiction is a judgment trigger. Left live config untouched.
Candidate fix for the judge (notes only, no behavior change): as user lumen, `python3 ops/wq3354-fleet-selfheal/selfheal_merge.py --seed <repo selfheal.yaml> --live /home/lumen/agents/fleet-selfheal/selfheal.yaml` then re-run --verify.

## Not verified
Whether wq#3546 contains the exact "should stay alerting" sentence the note attributes to it (its notes tail was about the merge-never-reached-production root cause). Did not block on this.
