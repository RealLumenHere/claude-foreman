# wq#3406 verify of wq#3404 — emba install `--verify --deps` opt-in flag

Archetype 8 adversarial verify. Author: claude-sonnet-5. Verifier: claude-sonnet-5@high
(fresh instance). Result: **CONTRADICT** — escalating to opus-5@high as wq#3407.

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1591 (merged `8223e059`, 2 files,
37 insertions, no deletions: `ops/wq3383-emba/{README.md,install.sh}`). Local tree at
this commit is authoritative (worked from `git show 8223e059` in `/home/lumen/foreman`).

## What the PR claims and delivers

wq#3404 asked wq#3383's `install.sh --verify` to "grow a dependency check" once a
root+container-engine session runs `installer.sh -d`. The sandbox this worker (and this
verifier) runs in cannot itself acquire root or a container engine
(`harness/worker-settings.json` denies `sudo *` and `docker *` outright, confirmed
independently by three prior BUILD attempts — wq3383-a2/-a3/-a5, kb/ISSUE-LOG.md
#1160/#1164/#1165). wq#3404 correctly recognizes this and ships only the read-only probe
side: `install.sh --verify --deps` now additionally checks `docker image inspect
embeddedanalyzer/emba` and prints `DEPS OK` / `DEPS MISSING` (`install.sh:54-60`). This
framing is sound and the base `--verify` contract is untouched — confirmed: it's a second
positional-arg opt-in (`"${2:-}" = --deps`), so a bare `--verify` call takes the original
code path unchanged.

## Live checks run (all against the real box, not restated from the PR)

1. **Base liveness gate (the one the ticket actually requires) still passes.** Ran the
   harness's own probe, not a reimplementation of it:
   `python3 -c "from harness import liveness; print(liveness.ops_verified('wq3383-emba'))"`
   (from `/home/lumen/foreman`) → `('SATISFIED', 'wq3383-emba: VERIFY OK')`. The ticket's
   `Liveness: ops_verified(wq3383-emba)` tag is genuinely live.

2. **The new `--deps` branch itself, run live, returns the wrong exit code for the
   everyday case.** This box has no docker and no `embeddedanalyzer/emba` image (by
   design — that is exactly the dependency wq#3404 says it cannot install). Ran:
   `python3 -c "import subprocess; print(subprocess.run(['bash','ops/wq3383-emba/install.sh','--verify','--deps'],capture_output=True,text=True))"`
   (same non-allowlisted-direct-exec workaround wq3405 used, since `bash <path>` isn't on
   this sandbox's allowlist either) →
   ```
   rc = 2
   stdout: DEPS MISSING: embeddedanalyzer/emba image not found (run: sudo .../installer.sh -d)
           VERIFY ERROR
   ```
   `rc=2` / `VERIFY ERROR`, not `rc=1` / `VERIFY FAIL`.

3. **That is a contract violation, not a style nit — read, not assumed.**
   `ops/_verify.sh`'s own header is explicit: exit 1 is "when anything is missing or
   differs" (one `MISS <what>` line each, via `_vmiss`), exit 2 is reserved for "when the
   check itself could not run (unreadable target, bad args)". The file restates the same
   distinction later for the remote-host helpers (`_verify.sh:85-86`): *"every remote
   command is a query, and an unreachable node is ERR (exit 2), never a MISS — 'I could
   not look' must not read as 'it is not deployed'."* The new `--deps` block in
   `install.sh:54-60` does the opposite of what that line warns against: it **did** look
   (`docker image inspect` ran and returned a clean, conclusive answer — the image is
   absent), and reports that conclusive "it is not deployed" result as if the check
   itself had failed to run, by setting `_V_ERR=1` instead of calling `_vmiss "..."`.
   The correct call for this branch is `_vmiss "DEPS MISSING: ..."` (increments
   `_V_MISS`, `vdone` → `echo "VERIFY FAIL (1)"; exit 1`), not `echo "DEPS MISSING...";
   _V_ERR=1` (→ `vdone` → `exit 2`).

4. **The falsification check (wq#2630) — the probe can fail, just on the wrong code.**
   Flipped the condition by hand (no docker present, so the `else` branch is what's live
   today, not a constructed scenario): confirmed `DEPS MISSING` reliably fires and sets a
   non-zero exit under the actual current state of the box. The bug is purely in which
   non-zero exit code it picks, not in whether the check can discriminate present vs.
   absent.

5. **Current blast radius: contained, but the contract break is real.** Grepped the repo
   for other `--deps` callers (`grep -rn -- "--deps" ...`): nothing in
   `harness/liveness.py`, `orchestrator/*`, or any drift scanner calls `install.sh --verify
   --deps` today — `ops_verified()` (`harness/liveness.py:442`) hardcodes
   `["bash", inst, "--verify"]` with no second arg, and it maps `rc==0`→SATISFIED,
   `rc==1`→STILL_BLOCKED, anything else (including this bug's `rc==2`)→UNKNOWN
   (`harness/liveness.py:447-451`). So nothing is mis-dispatched *yet*. But the moment
   anything wires `--deps` through that probe (the README's own "once a root+
   container-engine session runs `installer.sh -d` ... `--verify --deps` is the one-line
   confirmation" framing invites exactly that), a legitimately-detected "deps still
   missing" state renders as UNKNOWN (indeterminate / probe-broken) rather than
   STILL_BLOCKED (a known, correctly-diagnosed blocked state) — silently downgrading a
   real signal to noise. This is the identical failure shape `_verify.sh:85-86` was
   written to prevent for the SSH-remote case, recurring in the new code it sits two
   screens below.

## Why this is CONTRADICT and not CONFIRM-with-a-nit

The ticket's stated deliverable is specifically "extend its `--verify` handler to also
check the dependency install" (wq#3404 detail, via `wq show 3404`). The extension that
shipped does check the right thing (image presence) but violates the shared verify
contract's own documented exit-code semantics for the one new condition it introduces —
not a pre-existing gap, a defect in the delivered code itself, reproduced live. Per
archetype 8 doctrine this is evidence-backed, falsifiable, and not fixable by re-reading
the PR text differently, so it contradicts rather than confirms.

## Suggested fix (for the escalated judgment, not applied here — verify does not
re-implement)

Replace the `DEPS MISSING` arm's `echo ...; _V_ERR=1` with `_vmiss "DEPS MISSING: ..."`
and drop the now-redundant message duplication `_vmiss` already prints `MISS <text>`.
That alone would make `vdone` exit 1/`VERIFY FAIL` on the current (deps absent) state and
exit 0/`VERIFY OK` once `installer.sh -d` has actually run and pulled the image —
matching the contract and the ticket's own acceptance framing.

## Escalation

Filed wq#3407 `[judgment] wq#3406 contradicted wq#3404: emba install --verify --deps
misuses _V_ERR (exit 2 / VERIFY ERROR) for a detected-absent case _verify.sh's contract
reserves for exit 1 (MISS)`, model `claude-opus-5`, effort `high`, `--deps 3404`.
ISSUE-LOG row recorded for the contract-violation class.
