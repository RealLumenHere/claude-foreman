# wq#4359 — verify wq#4344 (PR#2245: resolve PR#2232 README add/add conflict, land bundle)

**Verdict: CONFIRM.** Every claim in wq#4344's done-notes and
`reports/wq4344_resolution_pr2232_merge.md` checks out against fresh git evidence. One
initial apparent contradiction (remote branch not deleted) turned out to be a stale local
`refs/remotes/origin/*` cache on my side, not a real discrepancy — resolved by
`git fetch origin --prune` and re-checked. Filed one unrelated latent-bug finding
(ISSUE-LOG #1467) surfaced while trying to run the ticket's prescribed liveness probe; it
predates wq#4344 and does not change the verdict.

## Re-read cold

wq#4344 claimed: merged `origin/main` into PR#2232's branch
(`feat/wq4329-project-direct-mcp-connections-per-machi-a2`), resolved the README add/add
conflict keeping wq#4341's corrected text verbatim, re-verified the "What's in this bundle"
section, landed PR#2232 as a squash merge, remote branch deleted, `git merge-tree` no longer
shows `CONFLICT`.

## Verification (fresh, independent)

1. **Merge commit exists and is a real two-parent merge.** `git log -1 --format='%H %P'
   7eeba9cc` → parents `a8d41035` (PR#2232 tip) and `8a0c7110` (main's tip at the time).
   `git show --stat 7eeba9cc` touches `ops/wq4329-direct-mcp-commander/README.md` (+58/-x)
   and nothing else under that directory — `install.sh` untouched, confirming the arg-order
   bug found below (see "Separate finding") predates wq#4344.
2. **README conflict resolved correctly, no markers, byte-identical to main's corrected
   text.** `grep -n '^<<<<<<<\|^=======\|^>>>>>>>'` on the current
   `ops/wq4329-direct-mcp-commander/README.md` (via `git show e5b1e347:...`) → no matches.
   `git diff 39002885:ops/wq4329-direct-mcp-commander/README.md
   e5b1e347:ops/wq4329-direct-mcp-commander/README.md` → empty (byte-identical). The
   retracted ISSUE-LOG#1016 "dead-end" text that appears in the file is framed as the
   *correction's own explanation of the wrong prior claim* (lines 15-28: "Amended
   2026-10-19 (wq#4341...)... That citation was wrong..."), not a reintroduction — matches
   wq#4342's earlier content verification, not re-litigated here.
3. **All four bundle files present on main.** `git ls-tree --name-only origin/main --
   ops/wq4329-direct-mcp-commander/` → `PHASE1-PAIRING-CHECKLIST.md`, `README.md`,
   `install.sh`, `remote-desktop-commander.service`. The README's "What's in this bundle"
   section (lines 79-92) describes exactly these three non-README files; all three now
   exist where previously (pre-wq4344) they were stranded on PR#2232's branch only.
4. **PR#2232 actually landed and branch cleaned up.** `git rev-parse --verify
   origin/feat/wq4329-...-a2` initially *appeared* to succeed against my stale
   `refs/remotes` cache — but `git fetch origin feat/wq4329-...-a2` returned `fatal:
   couldn't find remote ref`, and `git fetch origin --prune` then printed `[deleted] ... ->
   origin/feat/wq4329-project-direct-mcp-connections-per-machi-a2` among 17 pruned refs.
   The branch is genuinely gone on the forge; my first read was a local caching artifact,
   not evidence against wq#4344. (Flagging this so a future verifier doesn't skip the
   `--prune` step and ships a false contradiction.)
5. **Squash-landed commit is real and in main's ancestry.** `e5b1e347` ("feat(mcp): wq4329
   phase 1 direct mcp via remote desktop commander", single parent `4e623fad`, +84/-0
   across exactly the 3 non-README files) — `git merge-base --is-ancestor e5b1e347
   origin/main` → true. Note: wq#4344's done-notes label this landing "squash 7eeba9cc1c",
   which is actually the *merge*-commit hash from step 1 (two parents), not the squash
   commit that landed on main (`e5b1e347`, one parent). This is a labeling slip in the
   prose, not a landing defect — the actual tree state is correct regardless of which hash
   the notes called it.
6. **Liveness probe passes.** `git merge-tree origin/main
   origin/feat/wq4329-...-a2` (post-`--prune` fetch) → no `CONFLICT` line (the branch no
   longer exists to conflict against, which is the intended end state).
7. **ISSUE-LOG #1465 citation accurate.** Row 1465 reads exactly as wq#4344's notes
   describe: `lumen-pr comment to PR#2232 failed... broker token scope... lacks
   write:issue (HTTP 403)` — not fabricated.

## Minor loose end, not a correctness defect

Two near-duplicate docs-only commits exist (`2bf05b3c`, not in main's ancestry; `d2c5a826`,
in main's ancestry) carrying the identical `reports/wq4344_resolution_pr2232_merge.md`
content — looks like an artifact of a retried PR open/land cycle (PR#2245 itself). Content
is identical either way and the final landed state (`d2c5a826`) is correct, so this doesn't
affect the verdict.

## Separate finding, logged not fixed (ISSUE-LOG #1467)

Tried to run the ticket's own liveness line, `bash
/home/lumen/foreman/ops/wq4329-direct-mcp-commander/install.sh --verify`: refused
(`requires approval`) — the worker Bash allowlist has no `bash <script>`/`ops/*/install.sh`
shape, a long-standing, already-logged gap (ISSUE-LOG rows 452/464/467). Did not retry (per
wq#4359's own prior-attempt critique: this exact probe caused a permission loop last
attempt). Reading `install.sh` cold surfaced a second, more specific problem: `install.sh`
takes `NODE_USER` as `$1` (required) and only checks `--verify` as `$2`
(`install.sh:15,19`). Calling it as the ticket instructs — `--verify` alone — makes
`--verify` the value of `NODE_USER` and leaves `$2` empty, so the verify branch's condition
is false; the script would silently fall through to its real staging path instead
(`mkdir -p /home/--verify/staged-units`, write a service file templated for a `--verify`
"user" that doesn't exist). Substituted the one non-privileged piece of the --verify logic
I could run directly: `node --version` → v22.23.3, satisfies the `>= 18` check. The other
two --verify checks (staged unit file presence/templating) correctly report missing, since
Phase 1 staging hasn't been run yet — expected, out of scope for a README-merge verify-pass.
`install.sh` predates wq#4344 (merge commit `7eeba9cc` didn't touch it, per point 1 above),
so this is not a wq#4344 regression — filed as ISSUE-LOG #1467, not fixed here per
"do not re-implement."

## Not re-litigated

- Content accuracy of the README's A-vs-B comparison — already verified independently by
  wq#4342; unchanged by wq#4344's merge (byte-identical, point 2 above).
- Whether PR#2232's other files (`install.sh`, the service unit, the checklist) are
  individually correct/complete — out of scope for this merge-conflict-resolution
  verify-pass; they were authored under wq#4329/PR#2232, not wq#4344.
