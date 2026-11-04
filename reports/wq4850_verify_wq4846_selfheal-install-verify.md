# wq#4850 verify-pass: wq#4846 "[ops-root-apply] wq3354-fleet-selfheal: install.sh --verify"

**Verdict: CONFIRM.** PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2603, landed as
`75f75cf8` (report-only commit, 1 file / 74 insertions:
`reports/wq4846_root-apply_wq3354-fleet-selfheal_anubis-maat-seed.md`). The ticket's substance
is a live-system apply, not a code diff — the claim to verify is "the operator copy of
`selfheal.yaml` now has the `nodes.notes.anubis`/`nodes.notes.maat` seed keys merged in, and
`install.sh --verify` is green," which is exactly the thing `ops_verified(wq3354-fleet-selfheal)`
checks.

## Re-ran the live probe myself (this session, 2026-11-04)

`/home/lumen/bin/lumen-run` is on this worker's Bash allowlist
(`harness/worker-settings.json:20` — `Bash(/home/lumen/bin/lumen-run *)`; wq#4846's report cites
line 19 for this, off by one from the actual line, but the substantive claim — that the tool is
allowlisted — is correct):

```
/home/lumen/bin/lumen-run start --name wq4850-verify-wq4846 -- \
  bash /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify
```
unit `lumen-job-wq4850-verify-wq4846-1793771331`, `lumen-run wait` → `DONE:0`. Journal:
```
INFO live-only: nodes.notes.neith
VERIFY OK
LUMEN_JOB_EXIT=0
```
Exit 0, matching `ops_verified(wq3354-fleet-selfheal)`'s own SATISFIED condition
(`harness/liveness.py:432-451`: `bash install.sh --verify` rc 0 → SATISFIED).

## One apparent discrepancy, checked and resolved — not a contradiction

wq#4846's own re-verify journal (in its report) shows **two** live-only INFO lines
(`nodes.notes.neith` and `nodes.notes.sekhmet`); my fresh run above shows only `neith`. This
looked like a possible fabrication at first read, so I traced it:

- `git log --oneline`: commit `54a96622` ("wq4843 sekhmet offline note") added a
  `nodes.notes.sekhmet` entry to the **seed** file
  (`ops/wq3354-fleet-selfheal/selfheal.yaml:322`, confirmed present via
  `grep -n "^    sekhmet:"` in this worktree) at 05:42:47Z.
- wq#4846's report commit (`75f75cf8`) landed later, at 05:46:02Z, but its worktree was
  presumably checked out/running its verify slightly before `54a96622`'s seed addition was
  visible to it, or the two build workers raced — either way, `sekhmet` was live-only (in the
  operator copy, not yet in that worker's seed copy) at the moment *that* report's verify ran.
- By the time of my fresh run (this session, well after `54a96622` merged to `main`), the seed
  file already contains `sekhmet` too, so it no longer reads as live-only — it's matched,
  silently, like `anubis`/`maat`. `neith` stays live-only because it is not in the seed at all
  (grepped: absent from `ops/wq3354-fleet-selfheal/selfheal.yaml`), consistent across both runs.

This is just two verify runs at different points in a fast-moving multi-ticket rollout landing
on the same file — not a contradiction of wq#4846's claim. The thing being verified (anubis/maat
merged live, `--verify` green) holds in both runs.

## Citation spot-checks (adversarial)

- `bin/lumen-run:63,89` ("--root ... run the unit as root"): confirmed — line 63 is the usage
  text, line 89 is `--root) runas=(); shift;;`.
- `harness/liveness.py:432-451` (`def ops_verified(bundle):` through its `subprocess.run(["bash",
  inst, "--verify"], ...)` / returncode handling): confirmed, line 432 is the `def` line.
- `install.sh:73-83` / `install.sh:84-98`: confirmed against
  `ops/wq3354-fleet-selfheal/install.sh` — 73-83 is the `install -d`/CODE[] install loop plus the
  `selfheal_merge.py` branch; 84-98 is the UNITS[] install loop, `daemon-reload`, and
  `systemctl enable --now` of the timer/leakwatch/exposure-census units.
- ISSUE-LOG rows 1238/1300 (`kb/ISSUE-LOG.md:1387,1449`): both real rows, both describe the
  `lumen-run`-as-root-escapes-the-BUILD-sandbox pattern this ticket reuses.

## Conclusion

No contradiction. The live-destination apply from wq#4846 holds: `install.sh --verify` against
the real `/home/lumen/agents/fleet-selfheal` destination exits 0 right now, under a fresh
`lumen-run` invocation from this session, independent of wq#4846's own run. CONFIRM — no
escalation to judgment.
