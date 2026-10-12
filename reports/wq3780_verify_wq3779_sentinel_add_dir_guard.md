# wq#3780 — verify-pass of wq#3779 (PR#1780)

Author: claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance) · Verdict: **CONFIRM**

## What wq#3779 changed

`harness/primary_tree_guard.py`: added a documented, by-name exemption set
`NON_PRIMARY_ADD_DIRS = {"/home/lumen/sentinel"}`.

`harness/tests/test_primary_tree_guard.py::test_primary_trees_covers_every_agents_yaml_add_dir`:
subtracts `ptg.NON_PRIMARY_ADD_DIRS` from the set of agents.yaml default `add_dirs` before
asserting every one of them is reachable through `primary_trees()`.

Claim: `/home/lumen/sentinel` is a default add_dir (agents.yaml, wq#3554) but is deliberately
not a git repo and not meant to be a `primary_tree_guard`-tracked primary, so the coverage
test went red and needed a narrow, named exemption rather than a blanket "any non-git
add_dir" rule.

## What I checked

1. **agents.yaml**: `/home/lumen/sentinel` is listed under `defaults.add_dirs`
   (`harness/agents.yaml:47`, comment cites wq#3554) — claim holds.
2. **Filesystem reality of sentinel**: `/home/lumen/sentinel` has no `.git`, has a
   `docker-compose.yml`, and `data/gatus.db{,-shm,-wal}` (sqlite, Gatus) — matches the PR's
   description of a live non-git Gatus deployment. `config/config.yaml` exists (did not cat
   it — credential-shaped file, per house rule).
3. **Scope of the exemption**: grepped the repo for `NON_PRIMARY_ADD_DIRS` — the constant is
   referenced in exactly one place outside its own definition (the one test). It does not
   touch `guard.sh`'s separate branch-freeze `PRIMARY_TREES` fallback list (which also
   excludes sentinel already, consistently) or any runtime write-protection path. The fix is
   test-scope only; no production behavior changes.
4. **Pre-fix repro**: temporarily `git checkout e7724081 -- harness/primary_tree_guard.py
   harness/tests/test_primary_tree_guard.py` inside the real worktree (clean before/after,
   restored via `git checkout HEAD --`) and reran the single test. It failed exactly as
   described:
   `AssertionError: add_dirs missing from primary_trees(): ['/home/lumen/sentinel']`
   — confirms the bug was real and isolated to sentinel (not e.g. mailbot, which reaches
   `primary_trees()` via `router.LANE_REPO`).
5. **Post-fix**: restored HEAD, reran — `34 passed` in
   `harness/tests/test_primary_tree_guard.py`. Also ran the two other files that reference
   `primary_tree_guard` (`test_guard.py`, `test_worktree_module_identity.py`) alongside it:
   `464 passed in 95.17s`, no regressions.
6. **Adversarial probe — can the assertion still fail?** (wq#2630 acceptance-probe doctrine)
   Built a standalone probe adding a second, fake add_dir
   (`/home/lumen/totally-fake-add-dir-probe`) alongside sentinel and recomputed the same
   `missing` expression the test uses against the real `primary_trees()` /
   `NON_PRIMARY_ADD_DIRS`: it isolated exactly the fake entry
   (`missing == ['/home/lumen/totally-fake-add-dir-probe']`), proving the exemption is
   narrow (by name) and a genuine future gap still trips the test — it is not a blanket
   "non-git add_dir" bypass.

## Verdict

CONFIRM. The diff is minimal (2 files, +20/-1), the root-cause narrative checks out against
the live filesystem and agents.yaml, the pre-fix failure reproduces with the exact missing
entry claimed, the fix is test-scoped with no runtime behavior change, and the exemption
cannot silently swallow a future unrelated gap. No contradiction — no escalation to
opus-5@high needed.
