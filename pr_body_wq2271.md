## What

token-engine T1 (design §3.1-3.2): git credentials move behind the broker —
the recurring leak class (embedded tokens in remote URLs / stored-credential
files, wq#2243 / ISSUE-LOG 537).

- Helper `token-engine/git-credential-token-engine` (installed to
  `/usr/local/bin`): forwards git's credential `get` line protocol to the
  broker's new `git-credential` verb; `store`/`erase` are no-ops; it never
  writes a file. Grant self-attested per invocation (the lumen-pr T4
  pattern), kind `git_read`, node `repo:owner/name`.
- Daemon verbs `git-credential` / `git-fetch` / `git-push`
  (`git_read`/`git_write`, node derived from the slug like the PR family).
  The credential leaves the daemon only as a one-shot handoff file (the
  gateway-key pattern), so frames stay value-free and the mask tripwire
  stays intact; host and scheme are pinned to the forge the slug maps to.
  `git-fetch`/`git-push` are the zero-crossing path: git runs in the daemon
  (group-readable mirror under the runtime dir / scratch bare repo, auth in
  daemon env only, no `--force` spelling exists).
- Harness: `fworker.write_worktree_credential_config` writes per-worktree
  `credential.helper` (an empty entry first — it clears the inherited global
  `store` helper — then `token-engine`) and `credential.useHttpPath=true`
  into config.worktree, never `~/.gitconfig`.
- install.sh installs the helper, runs the wq#2243 sweep `--fix`, installs
  its hourly timer (ISSUE-LOG 540), and under `TE_FINALIZE_MOVE=1` retires
  the `.secrets` forge-token originals, the stored-credential file, and the
  global `store` helper line.

## Why

Design §3.1's "build both" call: the helper fixes the URL/config/history
leak surface for every git invocation the fleet already has; the verbs
remove the last crossing. The password served is minted where possible: a
GitHub App installation token (1 h TTL, repo-scoped, ledgered `github-app`
for ticket-end/reaper revocation) once forges.json carries a `github_app`
block; until that App exists (T0 prereq, Frank's account task) the
engine-held PAT is served — the same in-memory crossing as today, minus
every on-disk surface.

## How

`verb_git_credential` validates slug/protocol/host shape, resolves the forge
ladder (identity map first), refuses host/scheme mismatches, and answers
with `_handoff_write`'s 0640 one-shot file. `verb_git_push` reads
`refs/heads/<branch>` from the calling ticket's worktree (or a
`/home/lumen`-bounded `src`) into a scratch bare repo and pushes with an
`http.extraheader` Basic line carried in `GIT_CONFIG_*` env — never argv,
never a URL. `verb_git_fetch` maintains a bare mirror under the runtime dir,
chgrp'd to token-client, that workers fetch from by path.

## Testing

- `python3 token-engine/tests/test_daemon.py` — 57/57 (11 new: grant
  scoping, host/protocol pinning, value-free frame with a mask-shaped token,
  real pushes/fetches against `file://` remotes, helper no-op contract).
- `python3 token-engine/tests/test_ledger.py` — 10/10.
- `python3 token-engine/tests/run_replay.py t1` → `GATE_REPLAY_T1_PASS`: a
  real `git push` through helper→broker→handoff against a Basic-auth
  loopback forge (`tests/fake_git_forge.py`) from a repo carrying exactly
  the fworker-written config, with a userinfo-free remote; the token appears
  nowhere under the client repo, in env, or in the sweep; the handoff file
  is consumed.
- `fworker.write_worktree_credential_config` exercised against a scratch
  worktree: worktree effective helper list ends `['', 'token-engine']`,
  main checkout untouched.

## Risk / rollback

Additive until deployed: the verbs and helper are dormant without the root
install, and the worktree config degrades to the pre-T1 credential path when
the helper binary is absent (config write is best-effort by design — a git
oddity must not brick dispatch). Deploy is the documented root step
(re-run `token-engine/install.sh` detached); `TE_FINALIZE_MOVE=1` is the
only destructive step and is deferred until a live fleet push through the
helper is verified. Rollback: remove the worktree config lines and the
helper binary; the global `store` helper still works until finalize.

Refs: wq#2271

🤖 Generated with [Claude Code](https://claude.com/claude-code)
