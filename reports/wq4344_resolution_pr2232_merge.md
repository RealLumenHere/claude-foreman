# wq#4344 — resolve guaranteed add/add conflict between main and PR#2232's branch

**Resolution: merged main into PR#2232's branch, resolved the README conflict in favor of
wq#4341's corrected text, and landed PR#2232.** wq#4342 (opus-5@high: CONTEST, low severity)
found main had only `ops/wq4329-direct-mcp-commander/README.md` (landed standalone by
wq#4341/PR#2241, describing `install.sh`, `remote-desktop-commander.service`, and
`PHASE1-PAIRING-CHECKLIST.md` in present tense while they existed only on PR#2232's
still-open branch) and a reproducible add/add conflict: `git merge-tree <main-head>
feat/wq4329-project-direct-mcp-connections-per-machi-a2` → `CONFLICT (add/add): Merge
conflict in ops/wq4329-direct-mcp-commander/README.md`.

## What was done

1. Checked out PR#2232's branch (`feat/wq4329-project-direct-mcp-connections-per-machi-a2`,
   tip `a8d41035`) in a scratch worktree and merged `origin/main` (tip `990b303b`, i.e.
   commit `c8e18bbc`'s remote ref at the time) into it.
2. The merge auto-applied cleanly everywhere except the expected README add/add conflict.
   Resolved it with `git checkout --theirs` (main's side), keeping wq#4341's corrected text
   verbatim — confirmed byte-identical to `origin/main`'s copy before committing.
3. Re-verified the "What's in this bundle" section (README lines 79-92 on main) against the
   files actually present on the merged branch: `install.sh`,
   `remote-desktop-commander.service`, `PHASE1-PAIRING-CHECKLIST.md` all exist and the
   section's description of each was unchanged by the merge — no edit needed.
4. Committed the merge (`7eeba9cc`) and pushed it to update
   `feat/wq4329-project-direct-mcp-connections-per-machi-a2` on origin directly (not through
   `lumen-pr`, since this updates a feature branch, not main).
5. Re-ran `git merge-tree origin/main origin/feat/wq4329-project-direct-mcp-connections-per-machi-a2`
   post-push: no `CONFLICT` line — liveness probe passes.
6. Attempted a PR#2232 comment documenting the supersession via `lumen-pr comment`; the
   broker token for this ticket lacks the `write:issue` scope (HTTP 403, `token
   scope=write:repository,write:user`). Logged as ISSUE-LOG row 1465 (scope widening is a
   token-engine config change out of this ticket's scope, not a workaround to retry) and
   proceeded — the reasoning that would have gone in the comment is recorded here and in
   wq#4344's done-notes instead.
7. Landed PR#2232 via `lumen-pr land`: merged as squash commit `7eeba9cc1c` on the forge,
   remote branch `feat/wq4329-project-direct-mcp-connections-per-machi-a2` deleted on merge,
   primary tree fast-forwarded to `e5b1e347`.

## Verification after landing

- `git ls-tree --name-only origin/main -- ops/wq4329-direct-mcp-commander/` lists all four
  files: `PHASE1-PAIRING-CHECKLIST.md`, `README.md`, `install.sh`,
  `remote-desktop-commander.service`.
- `git show origin/main:ops/wq4329-direct-mcp-commander/README.md` is byte-identical to the
  pre-landing corrected text (wq#4341/PR#2241) — the ISSUE-LOG #1016 "dead-end" citation was
  not reintroduced.
- The PR#2232 branch ref no longer exists (deleted on merge), so there is nothing left for a
  future `git merge-tree` to conflict against.

Follow-up not in scope here: the PR#2232 comment from step 6 (blocked on broker scope,
ISSUE-LOG #1465).
