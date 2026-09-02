# wq#1271 — Adversarial verify-pass of wq#1255 (dispatch-batch + spawn hardening, PR#49)

**Verdict: CONTRADICT** — the outer batch-driver detachment uses exactly the primitive the
PR's own commit message and `lumen-run`'s header say does not survive the failure mode this
feature exists to fix.

## What was shipped (PR#49, commit 5748182 in lumen-bin, unmerged)

`dispatch-batch` (101 lines) + `tests/test_dispatch_batch.py` (215 lines, 4 tests). Purpose
per the commit message: fix ISSUE-LOG #18 ("Connector kills multi-spawn shell loops —
dispatches never execute") by (a) having the script re-exec itself detached so a caller can
exit instantly, and (b) launching each ticket's actual worker via the production path
(`router.py dispatch-cmd` → `lumen-run start`, a real systemd transient unit) instead of a
raw nohup/setsid child, with claim verification per ticket before reporting OK.

## Probe: tests pass, but don't exercise the claimed failure mode

```
$ cd _lumen-bin (worktree on origin/main + PR#49's commit, HEAD 5748182)
$ python3 -m pytest tests/test_dispatch_batch.py -v
4 passed in 11.56s
```

All 4 tests genuinely pass, reproduced independently against a fresh scratch `WQ_DB`. But
`test_returns_immediately_even_though_workers_keep_running` only proves the workers survive
their *immediate spawner exiting normally* — its own comment admits this (test_dispatch_batch.py:144-150):
"Not asserting a literal PPID of 1 ... What's checkable ... is that each worker outlived and
was reparented away from its immediate spawner." None of the four tests ever sends a signal
to the *batch driver's own process group/cgroup* — the actual trigger named in ISSUE-LOG #18
and in this PR's own commit message. The suite is green because it tests ordinary orphaning,
not the SIGKILL-to-pgroup/cgroup scenario the feature is built to survive.

## The contradiction

`dispatch-batch:94-101` is the self-detach step, run by every invocation before any ticket
work happens:

```bash
mkdir -p "$RUN_DIR"
DISPATCH_BATCH_CHILD=1 setsid nohup "$0" "$@" </dev/null >/dev/null 2>&1 &
disown
echo "dispatch-batch: dispatching $# ticket(s) detached — logs under $RUN_DIR"
```

This is a bare `setsid nohup ... & disown` — and the entire `run_batch()` loop (iterating
every ticket, including up to `CLAIM_WAIT_S` = 60s of claim-polling *per ticket*,
dispatch-batch:25,75-88) runs inside this weakly-detached child, not inside anything
systemd-managed.

`/home/lumen/bin/lumen-run:2-8`, cited by this PR's own commit message as the reason it
routes *per-ticket* launches through `router.py dispatch-cmd` → `lumen-run start` instead of
a raw nohup/setsid child:

> lumen-run — launch/track long jobs over Commander so they survive the dc_stateless_leak_patch
> process-group reaping ... the mcpw/supergateway gateway is stateless and reaps the child
> process-group when an MCP call closes/flaps. **nohup/setsid/disown do NOT survive this**
> (SIGKILL to the pgroup / cgroup can't be caught). systemd-run reparents the job to PID 1's
> tree, outside the gateway's reach.

So the PR's own cited authority says nohup/setsid/disown do not survive the exact event this
feature defends against — and the PR uses nohup/setsid/disown, unmodified, for the outer
driver loop that is supposed to keep running across the whole batch. The per-ticket launch
correctly escapes (each `lumen-run start` call creates its own systemd scope/cgroup); the
*coordinating loop that calls it* does not. `setsid` moves the child to a new session/process
group (escapes a pgroup-targeted kill) but does **not** move it to a new cgroup — a
cgroup-wide reap of the interactive session's slice (what lumen-run's header actually
describes: "SIGKILL to the pgroup / **cgroup**") kills it regardless of session/pgid. The
dispatch-batch docstring's own claim ("reparented to PID 1 regardless") is a different,
weaker property (parent-death orphaning) than cgroup escape, and conflates the two.

Practical consequence: if the gateway reaps the interactive session's cgroup at any point
after the caller's shell returns (which per lumen-run's header can happen on ordinary MCP
call close/flap — the exact moment `dispatch-batch` is designed to survive) while `run_batch`
is still mid-loop — e.g. still polling ticket 3 of a 5-ticket batch — every ticket from that
point forward never gets spawned. That is ISSUE-LOG #18's original symptom
("dispatches never execute"), reintroduced one layer up, for any batch where the total
claim-wait exposure (up to `N × 60s`) outlasts the interactive session.

Note ISSUE-LOG #18's own recorded solution text ("nohup setsid script — worked every time")
sits in tension with lumen-run's header — the KB itself isn't fully consistent on whether
nohup+setsid alone is sufficient. But the PR under review resolves that tension
*inconsistently within itself*: it accepts lumen-run's stronger claim (systemd-run required)
for the per-ticket launch, while exempting its own outer loop — which faces the identical
trigger — without comment or justification.

## The fix already exists in-repo and wasn't used here

`lumen-run start -- CMD [ARGS...]` is designed for exactly this: launch an arbitrary command
as a detached, genuinely cgroup-escaped systemd transient unit. Swapping
`dispatch-batch`'s self-detach line for something like:

```bash
DISPATCH_BATCH_CHILD=1 lumen-run start --name "dispatch-batch-$$" -- "$0" "$@"
```

would give the outer driver the same protection this PR already grants each individual
ticket, using the mechanism the PR's own commit message argues is the only one that survives.

## Other checks — no issues found

- `default_spawn`'s `eval "$cmd_line"` (dispatch-batch:31-37) evals `router.py dispatch-cmd`'s
  stdout; confirmed (`router.py:899`) that output is `shlex.quote`d per-arg and built only
  from ticket id / resolved repo / model / effort / archetype — no free-text ticket
  title/detail is interpolated, so no injection surface despite the `eval`.
  This path is also never exercised by the test suite (`DISPATCH_BATCH_SPAWN_CMD` overrides
  it in all 4 tests) — untested, but not defective on inspection.
- Claim-verification loop, UNIT= parsing (last matching line, robust to leading stub output),
  per-ticket log tee'ing, and the "one spawn failure doesn't abort the batch" behavior all
  check out against their tests and against direct reading — reproduced independently.

## Disposition

Do not land as-is. The claim-verification and production-launch-path hardening (the
"spawn hardening" half of this ticket) is sound. The "dispatch-batch" self-detach half
re-introduces the bug it's named after fixing, contradicting the reasoning the same commit
uses to justify its own per-ticket launch mechanism. Recommend routing the outer re-exec
through `lumen-run start` (already available, already proven, already the standard this repo
holds every other detached job to) instead of raw `setsid nohup ... & disown`.
