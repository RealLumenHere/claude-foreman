# wq#2964 — adversarial verify-pass of wq#2943 (judgment: wq#2932 contradicted wq#2910 — backup-important op)

Verifier: claude-sonnet-5 @ high, fresh instance. Worktree `wq2964-a1`. 2026-09-28.
Subject: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/1261 (claude-foreman commit
`cff8788`, ops bundle + report) and its companion `lumen-bin` PR #167 (commit `82ce01e`,
`wq done` fix) — both cited as one judgment in `reports/2943_judgment_wq2910_undeployed_ops_bundle.md`.
Author: claude-opus-5.

## Verdict

**CONFIRM.** All four rulings hold up under independent reproduction. No contradiction found.

## What the diff actually is

- `claude-foreman@cff8788` (+179/-8, 3 files): `ops/wq2910-a1-backup-important-hardening/install.sh`
  gets a `--verify` handler per `ops/VERIFY_CONTRACT.md` (`vfile` on the logrotate drop-in,
  `vunit_file` on the `90-onfailure-alert.conf` systemd drop-in); the README's install line is
  corrected (`lumen-run --root -- bash ...` is not a shape today's `lumen-run` accepts — `--root`
  is a flag of `start`, not a bare `lumen-run` option); plus the judgment report itself.
- `lumen-bin@82ce01e` (+380 lines, 2 files, no deletions): a `pr_ops_deploy_check()` /
  `ops_verify_handler()` / `_local_bundle_installs()` / `pr_changed_files()` stack, spliced into
  `cmd_done`'s existing `kind == "url"` branch (`wq:2905`, post-fix line numbers) right after the
  merge-state check, so a PR-URL citation now runs the same "is the bundle actually live"
  question the `kind == "path"` branch already asked. Plus `tests/test_wq2943_ops_deploy_url_gate.py`
  (237 lines, 7 tests).

## Checked against the diff cold

- **Live verify handler, run independently.** The ticket's own liveness probe:
  `lumen-run start --name wq2964-verify -- bash /home/lumen/foreman/ops/wq2910-a1-backup-important-hardening/install.sh --verify`
  → `VERIFY OK`, exit 0 (unit log: `lumen-job-wq2964-verify-1790587188.service`). Confirms Ruling
  2 (installed and live) and Ruling 3 (handler present and correct) on the actual box, not from
  the report's own transcript.
- **`_local_bundle_installs` primary-vs-worktree preference is real, not asserted.** Read
  `_sha_repo_candidates()` (`wq:2095`, pre-existing) — it scans `os.getcwd()` plus top-level git
  checkouts under `/home/lumen`, `/home/lumen/projects`, `/home/lumen/agents`. In the real
  dispatch shape (a build worker running `wq done` from inside its worktree), that yields both
  the worktree (`.git` is a file) and the primary tree (`.git` is a directory) as candidates; the
  loop in `_local_bundle_installs` explicitly prefers the directory-`.git` hit and `break`s on it.
  Matches the stated intent ("the liveness probe runs the primary tree's copy, never a
  worktree's") and is consistent with `ops/VERIFY_CONTRACT.md`'s existing rule for the standalone
  liveness probe.
- **Helpers the new code calls but doesn't define all pre-exist and match**: `_forge_get`
  (`wq:2311`), `_sha_repo_candidates` (`wq:2095`), `OPS_INSTALL_RE` (`wq:2666`), `_forge_token`
  (`wq:2287` — a masking pipe used during this review displayed this as a literal `MASKED(...)`
  call; confirmed via direct `Read`/`grep` on the real file that the source says `_forge_token`,
  not a masking bug in wq2943's diff, just an artifact of the review tooling).
- **Fail-open posture matches wq#899's existing precedent**: `pr_changed_files` returns
  `(None, why)` on any forge trouble (no token, non-2xx, non-list response), and the caller
  (`pv, pwhy = pr_ops_deploy_check(...)`) only warns (`elif pwhy: WARNING ... Closing anyway`) —
  never blocks — when the reason is forge trouble, and stays silent when the PR simply touches no
  bundle (`pv == "none"`, `pwhy == ""`). Traced by reading, not assumed.
- **Marker-gated execution, not a bare flag pass.** `ops_verify_handler` re-checks
  `_ops_bundle_has_verify` before invoking `bash <install.sh> --verify` via subprocess (60s
  timeout, `stdin=DEVNULL`), so a bundle whose script doesn't declare the `--verify` marker is
  never executed with an unexpected first positional arg. This is the same guard
  `ops-deploy-drift` uses, so both tools agree on which bundles are checkable — as the docstring
  claims.

## Adversarial checks that did *not* find a contradiction

- **Multi-bundle PR + a single `--deployed`/`--deploy-verify` override**: `pr_ops_deploy_check`
  applies the *same* caller-supplied `deployed_target`/`deploy_verify` pair to every bundle a PR
  touches (there's no per-bundle override). A PR touching two bundles where only one has a
  `--verify` handler would need the manual override to happen to match the handler-less bundle,
  or it fails closed. This is a real but narrow limitation — it carries over an equivalent
  single-artifact assumption the `kind == "path"` branch already has — and pushes toward the
  contract's own recommended fix (give the bundle a `--verify` handler) rather than papering over
  it. Not a regression, not blocking.
- **Path-traversal via a crafted PR file list**: `_local_bundle_installs` does
  `os.path.join(root, rel)` on filenames the forge API reports, and `OPS_INSTALL_RE.search` is
  unanchored so a `../`-laden path could in principle match. Git tree objects cannot contain `..`
  path components in a normal commit, so this isn't reachable through the ordinary PR-diff path
  the code assumes; noting it as a theoretical hardening item, not a contradiction.
- **Truncated file lists**: `PR_FILES_MAX_PAGES = 5` (500 files) with no truncation warning if a
  PR has more changed files than that and still returns full pages. Vanishingly unlikely for the
  ops-bundle PRs this gate targets; not something wq#2910/wq#2932/wq#2943 turned up, and not
  reproduced as a live problem here.

## Reproduction (this pass, independent)

Did not touch `/home/lumen/bin`'s real checkout (still correctly on `fix/wq2908-github-mirror-sync`
with another lane's uncommitted work, per Ruling 4's own disclosure and confirmed live via
`git -C /home/lumen/bin branch --show-current` / `status --short`). Instead built a disposable
`git worktree add /tmp/.../verify-worktree origin/main --detach` off the real `lumen-bin` repo,
swapped `wq` between the pre-fix (`82ce01e^`) and post-fix (`82ce01e`) blobs, and ran the diff's
own tests directly (no re-implementation), removing the scratch worktree afterward:

- Post-fix `wq`, `pytest tests/test_wq2943_ops_deploy_url_gate.py -q` → **7 passed**.
- Pre-fix `wq` (same test file, unchanged), same command → **6 failed, 1 passed** — the one pass
  is `test_control_pr_touching_no_ops_bundle_is_unaffected`. Matches the report's claim exactly.
- Both pre-fix and post-fix `wq`, `pytest tests/test_wq_done_gate_terms.py tests/test_wq_evidence_durability.py -q`
  → **9 failed, 28 passed**, identical 9 failing test names both times. Confirms these are
  pre-existing/environmental (traced to running under a linked-worktree cwd, which the evidence-
  durability gate itself flags: `"exists only inside the linked worktree /home/lumen/worktrees/..."`)
  and not a regression introduced by this diff. Pass count (28) differs from the report's stated
  39 — attributable to this review's environment (run from inside a worktree) rather than a
  discrepancy in the diff's correctness; the *set* of failing names matched exactly pre/post,
  which is what the claim ("unrelated, pre-existing, reproduces identically") depends on.
- `wq#2961` (the disclosed follow-up for ff'ing `/home/lumen/bin`'s primary tree) exists,
  is claimed, and cites the exact confirm commands (`grep -c 'wq#2943' wq`,
  `pytest tests/test_wq2943_ops_deploy_url_gate.py`) — the report's "not yet live" admission is
  on the record, not swept under.

## Gate (this pass, final state)

- `bash ops/wq2910-a1-backup-important-hardening/install.sh --verify` (via `lumen-run`, on the
  live box) — VERIFY OK, exit 0.
- `pytest tests/test_wq2943_ops_deploy_url_gate.py -q` against post-fix `wq` — 7 passed; against
  pre-fix `wq` — 6 failed, 1 passed (probe can fail, and did, on the un-patched binary).
- `pytest tests/test_wq_done_gate_terms.py tests/test_wq_evidence_durability.py -q` — same 9
  failing names pre- and post-fix; no regression attributable to this diff.
- `wq show 2961` — follow-up ticket for the disclosed "not yet live" primary-tree gap is real,
  claimed, and correctly scoped.

## Board

wq#2943 verify-pass: **CONFIRM**. Two non-blocking observations recorded above (single
`--deployed`/`--deploy-verify` override shared across a multi-bundle PR; unanchored
`OPS_INSTALL_RE` match on a forge-reported path) — neither reproduces as a live problem and
neither contradicts the diff's core claims. No escalation to opus-5@high judgment.
