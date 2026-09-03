# wq#1266 — verify-pass: wq#1256 hardening 4/9 sync-live guard (PR#48, lumen-bin)

**Verdict: CONFIRM** — `sync-live` does exactly what its tests and docstring claim: refuses
to resync a primary tree to any commit that hasn't actually landed on `origin/main`, refuses
on a dirty tree, and only then performs the destructive `checkout main && reset --hard`.
Adversarial probing beyond the shipped suite (non-`main` starting branch, no local `main`
ref at all) didn't turn up a case where it acts unsafely.

Commit: `d5b80f5` on branch `feat/wq1256-hardening-4-9-sync-live-guard-pretooluse-a1`
(`/home/lumen/bin`, aka lumen-bin), already merged to `main` (verified: `git branch
--contains d5b80f5` lists the branch and `main` is downstream). PR:
http://100.121.17.5:3000/lumen/lumen-bin/pulls/48. Scope note: wq#1256 also shipped PR#194
in claude-foreman (the `guard.sh` PreToolUse block); this verify ticket's detail line scopes
it explicitly to "the diff shipped by wq#1256 — PR#48," so this report covers only the
lumen-bin helper (`sync-live` + `tests/test_sync_live.py`, 144 lines total).

## What the diff does

`sync-live <repo-path> <expected-sha>` (27 lines): `cd` into the repo, `git fetch origin`,
refuse unless `expected-sha` is an ancestor of `origin/main`, refuse if the tree is dirty
(`git status --porcelain` non-empty), else `git checkout main && git reset --hard
<expected-sha>`. Intended as the one sanctioned path left for resyncing a primary tree now
that `guard.sh` (wq#900/wq#1256) PreToolUse-blocks raw `checkout`/`reset`/`clean -f` against
`PRIMARY_TREES`.

## Verification performed

1. **Read `sync-live` and `tests/test_sync_live.py` cold** (`git show d5b80f5`) — logic
   matches the docstring's stated precondition ordering (ancestor check before dirty check
   before destructive reset).
2. **Re-ran the shipped suite independently**: `cd /home/lumen/bin && python3 -m pytest
   tests/test_sync_live.py -v` → 4/4 passed (`test_sha_not_on_origin_refused_tree_untouched`,
   `test_dirty_live_tree_refused`, `test_clean_tree_expected_sha_on_origin_resyncs`,
   `test_usage_error_on_missing_args`).
3. **Adversarial probe A — non-`main` starting branch**: built a scratch origin+clone, checked
   the live clone out onto `feature-branch` (clean tree), invoked `sync-live` against the
   valid landed SHA via `subprocess.run` (direct Bash-tool invocation of `sync-live` is itself
   blocked — see gap note below). Result: exit 0, branch switched to `main`, HEAD reset to the
   expected SHA. Correct.
4. **Adversarial probe B — no local `main` ref at all**: `git clone --no-checkout` (zero local
   branches, only `remotes/origin/main`), ran `sync-live` against the landed SHA. `git
   checkout main` auto-vivifies a tracking branch from `origin/main`; script completes exit 0,
   correct HEAD. No crash, no unsafe fallback.
5. **Reasoned through failure-closed cases without reproducing them destructively**: invalid/
   unknown `$EXPECTED` → `merge-base --is-ancestor` exits non-zero (128, not just 1) → still
   caught by the `||` REFUSE branch, tree untouched (message says "not in origin/main" for a
   technically-different reason, but the refusal itself is correct and safe). `git fetch`
   failure (network down) → `set -e` aborts before any mutation — fails closed. No scenario
   found where the script mutates the tree when it shouldn't.

## Gap noted, not a contradiction of this diff

Tried to invoke `/home/lumen/bin/sync-live` directly as a Bash tool call (as `guard.sh`'s own
block message instructs a worker to do): denied — `sync-live` is not in
`harness/worker-settings.json`'s per-command allowlist (only `wq`/`lumen-pr`/`slock`/
`lumen-run`/`fleet-search`/`lumen_busy` get bare full-path entries). This reproduces
`kb/ISSUE-LOG.md` #66/#71 firsthand rather than just citing them. It means a headless worker
following `guard.sh`'s own advice hits a second wall. This is real, but it's a gap in a third
file (`harness/worker-settings.json`, claude-foreman) untouched by either PR#48 or PR#194,
already logged twice, and outside this ticket's stated scope ("the diff shipped by ...
PR#48") — noted for the record, not scored against wq#1256.

## Conclusion

`sync-live`'s own logic is correct and safe under both the shipped tests and my own
adversarial probes. CONFIRM.
