# wq#3583 verify: wq#3582 root-apply — run wq3354-fleet-selfheal install.sh

Verdict: **CONFIRM**

Ticket: wq#3582 (lane `ops`, no code change, no PR — a root-apply/deploy ticket).
Claim: deploy foreman `a75f698b` (PR#1665 / wq#3577 dedup fix, already merged to
`main`) onto the live Lumen host by running
`ops/wq3354-fleet-selfheal/install.sh --no-enable`, then `install.sh`, then
`install.sh --verify`, each as root via `lumen-run --root`, landing
`VERIFY OK` where wq#3580 had previously measured `VERIFY FAIL (2)` (MISS on
both `selfheal_common.py` and `sh_providers.py`, pre-deploy).

This is an ops-apply ticket, not a diff — nothing to re-implement. Verify-pass
scope is: did the apply actually happen, and does the live target now match
the shipped bundle.

## What I checked

1. **Re-ran the live verify probe myself**, fresh, via
   `lumen-run start -- bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify`
   (unit `lumen-job-wq3583-verify-1791378014`) → `VERIFY OK`, `LUMEN_JOB_EXIT=0`.
   This re-runs every `vfile`/`vunit_file`/`venabled`/`vactive` check in
   `ops/_verify.sh` live — `vfile` does a byte-identical `cmp -s` between the
   shipped bundle file and the deployed target (with a superseded-file escape
   hatch that doesn't apply here, since `/home/lumen/agents/fleet-selfheal`
   isn't a git repo of its own). A stale or fabricated "VERIFY OK" in wq#3582's
   notes could not have survived a second, independent exit-0 run against the
   live target.

2. **Independently confirmed the fix is actually in the deployed bytes.**
   `/home/lumen/agents/fleet-selfheal/{selfheal_common.py,sh_providers.py}` are
   outside this session's allowed directories, so I couldn't `grep` them
   directly — but `vfile`'s `cmp -s` against the source bundle
   (`foreman/ops/wq3354-fleet-selfheal/{selfheal_common.py,sh_providers.py}`,
   which I *can* read) is exactly the stronger claim: byte-identical, not just
   "contains the function." I confirmed the source bundle itself carries the
   wq#3577 fix (`selfheal_common.py:146` defines `find_open_ticket`;
   `sh_providers.py:193` calls `ctx.out.find_open_ticket([label, "balance"])`),
   and `VERIFY OK` means the deployed copies match those bytes exactly.

3. **Checked unit state independently of the verify script**, not just trusting
   its `venabled`/`vactive` arms:
   `systemctl is-enabled`/`is-active lumen-fleet-selfheal.timer` → `enabled`/`active`;
   same for `lumen-leakwatch.service` → `enabled`/`active`. Matches wq#3582's
   notes and the verify script's own result.

4. **Checked wq#3582's own apply-time job units are still live in the journal**,
   rather than trusting its notes at face value:
   `lumen-run status` on `lumen-job-wq3582-noenable-1791377839`,
   `lumen-job-wq3582-enable-1791377853`, and `lumen-job-wq3582-verify-1791377869`
   all returned `DONE:0`; the verify job's journal independently shows the same
   `VERIFY OK` / `LUMEN_JOB_EXIT=0` wq#3582 claimed, timestamped at the time it
   claims to have run. This rules out the notes describing a run that didn't
   happen or silently failed.

5. **Cross-checked against wq#3580's pre-deploy baseline**: wq#3580 measured
   `VERIFY FAIL (2)` with `MISS differs` on both code files before wq#3582's
   apply. The before/after pair (FAIL → OK, same probe, same files) is
   consistent with a real deploy closing exactly the gap wq#3580 found, not a
   probe that was already passing or a no-op.

## Adversarial angles tried

- Could `--verify`'s `cmp -s` be fooled by a file that merely matches in
  the places wq#3580 happened to check? No — `vfile` does whole-file
  byte-identical comparison, not a substring/function-name check.
- Could the `_vsuperseded` escape hatch in `_verify.sh` paper over a real
  mismatch (e.g. deployed file diverged but "superseded" lets it pass)?
  No matching log line (`OK superseded: ...`) appeared in either my run or
  wq#3582's own verify run — only `VERIFY OK` with no per-file lines at all,
  meaning every `vfile`/`vunit_file` call returned clean with nothing to report.
- Could the units be enabled/active from a stale pre-existing state unrelated
  to this deploy, masking a code-only failure? No — `--verify` checks the code
  files and the units independently; the code files are what changed here
  (wq#3580 confirmed the units were already fine pre-deploy), and they now
  come back clean too.

No contradiction found. Confirming on the board.
