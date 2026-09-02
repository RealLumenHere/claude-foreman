# wq#1307 — Adversarial verify-pass of wq#1284 (dispatch-batch helper + spawn hardening, PR#217)

**Verdict: CONTRADICT** — same root-cause class the sibling verify-pass (wq#1271, PR#207)
already found and disposed against: `start_new_session=True` (Python's `setsid`) escapes a
process-group-targeted signal but does **not** escape a cgroup-wide reap, and the actual
mechanism this ticket is named for (ISSUE-LOG #18 "connector recycle") is documented
in-repo as a cgroup reap, not a pgroup one. wq#1284 also mischaracterizes the
"dispatch-batch half" of the ticket as already resolved when the live script still has the
exact defect wq#1271 contradicted.

## What was shipped (PR#217, commits a1754b0/516741d, merged to main)

`harness/fworker.py` — `run_child()`'s `subprocess.Popen` spawning the `claude` child now
passes `start_new_session=True`. New test `harness/tests/test_wq1284_spawn_hardening.py`
(2 tests: Popen kwargs assertion, and a live pgid-differs-from-parent check).
`wq#1284`'s done notes additionally claim the "dispatch-batch" half of ISSUE-LOG #18 is
"already done+live via wq#1255/lumen-bin PR#49 (verified byte-identical to merged commit
9be9b08, clean tree)" — no code change made there, just a re-verification.

## Probe: tests pass, but assert the wrong property

```
$ cd harness && python3 -m pytest tests/test_wq1284_spawn_hardening.py -v
tests/test_wq1284_spawn_hardening.py::test_run_child_passes_start_new_session PASSED
tests/test_wq1284_spawn_hardening.py::test_child_lands_in_its_own_process_group PASSED
2 passed in 0.20s
```

Both pass, reproduced independently. But both only check that the child's **pgid** differs
from the caller's — neither sends a signal to the parent's **cgroup** (the actual named
threat) and checks the child survives it. Same gap `wq#1271` flagged in the sibling PR's
test suite (`test_dispatch_batch.py`): green because it tests process-group escape, not the
cgroup-reap scenario the fix is named for.

## The contradiction

`fworker.py:950-957`, the whole of this ticket's in-harness change:

```python
# ISSUE-SOLUTIONS-fable.md #18: start_new_session=True puts the child in its own
# session/process group so a signal delivered to fworker's process group (e.g. an
# interactive connector recycle killing the whole group) does not take the `claude`
# child down with it — the same detach discipline dispatch-batch applies one level up.
proc = subprocess.Popen(
    cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, bufsize=1, start_new_session=True,
)
```

`fworker-wq<ID>` — the process this `Popen` call runs inside — is itself launched as a
systemd transient unit, not a bare process: `orchestrator/router.py:811`,
`cmd = ["lumen-run", "start", "--name", f"fworker-wq{ticket_id}", ...]`. `lumen-run`'s own
header (`/home/lumen/bin/lumen-run:2-8`) is the authority this exact ticket's spec
(`ISSUE-SOLUTIONS-fable.md` §3/#18) and the sibling PR both point to, and it says plainly:

> the mcpw/supergateway gateway is stateless and reaps the child process-group when an MCP
> call closes/flaps. **nohup/setsid/disown do NOT survive this** (SIGKILL to the pgroup /
> cgroup can't be caught). systemd-run reparents the job to PID 1's tree, outside the
> gateway's reach.

`lumen-run` does not override `KillMode` (`grep -n KillMode /home/lumen/bin/lumen-run` —
no hits), so every `fworker-wq<ID>` unit runs under systemd's default
`KillMode=control-group`: stopping/killing the unit sends the signal to **every process in
its cgroup**. `start_new_session=True` moves the `claude` child to a new session/process
group; it does **not** move it to a new cgroup — cgroup membership is inherited from the
parent regardless of `setsid()`. So a cgroup-wide reap of `fworker-wq<ID>` (an MCP gateway
recycling the session that launched it, or the unit's own designed stop path) kills the
`claude` child right along with `fworker`, exactly as before this patch.

This isn't hypothetical for this codebase specifically — `router.py:712-724` documents that
cgroup-wide kill of `fworker-wq<ID>` units as the *intended* design, not an edge case:

> OOMPolicy=stop (not lumen-run's "continue" default): on an OOM kill anywhere in the unit's
> cgroup, cleanly terminate the WHOLE unit (fworker + child) via the normal
> TimeoutStopSec-graced stop path below, rather than leave a supervisor alive over a cgroup
> that just lost a process — §E prefers external verdict-aware supervision (the lease
> reaper, wq#647) picking up a cleanly-stopped unit over relying on fworker's own in-process
> OOM handling.

So the system is deliberately built so a stop/OOM takes fworker and its child down
*together*, with the lease reaper picking up the cleanly-stopped unit afterward. wq#1284's
fix defends against a narrower, different event (a raw process-group-targeted signal that
does *not* touch the cgroup) that isn't the one named in ISSUE-LOG #18 or in `lumen-run`'s
own header, and isn't demonstrated to occur anywhere in this repo. In the one place it
*would* help — a signal that hits the pgroup but not the cgroup — it works against the
documented §E design: the child now outlives a cleanly-stopped unit as an untracked orphan
(no watchdog: `wall_clock_s`/`frame_gap_s`/turn-backstop all live in `run_child()`'s loop,
which died with `fworker`), and the lease reaper's "pick up a cleanly-stopped unit" model
assumes the unit's processes are actually gone — a later redispatch of the same ticket onto
the same worktree while the orphaned `claude` child is still mutating it is a real race this
change newly introduces.

## The mischaracterized "already done" half

wq#1284's done notes: "dispatch-batch half of #18 was already done+live via wq#1255/
lumen-bin PR#49 (verified byte-identical to merged commit 9be9b08, clean tree)." Checked
independently:

```
$ git -C /home/lumen/bin log --oneline -- dispatch-batch
9be9b08 feat(dispatch-batch): detached batch spawn with claim verification
```

One commit, still live on `main`, unchanged. This is the *exact* commit `wq#1271`
(`reports/1271_verify_wq1255_dispatch_batch_hardening.md`, this repo) issued a CONTRADICT
disposition against ("Do not land as-is") for precisely this class of bug: its outer
self-detach (`dispatch-batch:99-100`, still present verbatim —
`DISPATCH_BATCH_CHILD=1 setsid nohup "$0" "$@" </dev/null >/dev/null 2>&1 & disown`) uses
the same setsid-only primitive `lumen-run`'s header says doesn't survive a cgroup reap, for
the very loop that's supposed to keep dispatching across the whole batch. `9be9b08` was
committed 2026-09-02 11:32:16, thirty seconds before `wq#1271` was even created
(11:32:48) — it merged before the contradiction was found and was never revised afterward.
wq#1284 cites its continued existence as evidence the item is "done," not as an open,
already-flagged defect — the "already done+live" framing is not accurate.

## Other checks — no issues found

- The `start_new_session=True` change itself is syntactically correct, doesn't alter
  `stdin`/`stdout`/`stderr` wiring, and the returned `proc.pid` semantics (`os.getpgrp()`
  in the child equals its own pid, group leader) are correctly asserted by the new test.
- `proc.send_signal(signal.SIGTERM)` / `proc.kill()` in the watchdog-kill path
  (`fworker.py:1050-1062`) already targeted only `proc.pid`, not the group, both before and
  after this change — no regression there specifically.
- Full test file review: no injection surface, no resource leak in the test fixtures
  themselves.

## Disposition

Do not treat as resolving ISSUE-LOG #18 for `fworker`'s child spawn. The
`start_new_session=True` change is harmless but addresses a threat model this repo's own
`lumen-run` header and `router.py` §E design say is not the one that actually recurs
(cgroup reap, not pgroup signal) — and in the narrow case it does help, it fights the
documented "fworker + child die together, lease reaper recovers" design rather than
supporting it. The dispatch-batch outer-loop defect `wq#1271` already found is still live
and unfixed; this ticket's claim that it's "already done" should not stand. Recommend: (a)
route `fworker-wq<ID>`'s own launch and/or a supervisor-level watchdog through something
that can distinguish "cgroup died, child should die too" (already true today) from
"fworker crashed but the cgroup didn't," rather than trying to keep the child alive
independent of its own cgroup; (b) apply `wq#1271`'s recommended fix
(`lumen-run start` for `dispatch-batch`'s outer re-exec) to close the still-open half.

Escalating per ticket protocol: same-model-class contradiction (author claude-sonnet-5,
verifier claude-sonnet-5@high) → opus-5@high judgment trigger.
