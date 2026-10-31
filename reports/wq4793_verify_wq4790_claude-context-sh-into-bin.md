# wq#4793 — verify-pass of wq#4790 (PR#2571)

**Author:** claude-sonnet-5 (fworker-wq4790) · **Verifier:** claude-sonnet-5@high (fresh instance, fworker-wq4793)
**Repo:** /home/lumen/foreman · **Diff under test:** `3cf2e8f2` (merge of PR#2571,
`http://100.121.17.5:3000/lumen/claude-foreman/pulls/2571`)

## Scope

wq#4790 moved the real `claude_context.sh` boot script out of the untracked,
gitignored `minerva/context/` into a tracked bundle
(`ops/wq4790-claude-context-sh-into-bin/`) with an idempotent `install.sh`, replacing
the old 2-line `exec` shim that used to live at `/home/lumen/bin/claude_context.sh`.
Content/behavior was claimed unchanged — only the canonical home moved.

Diff (`git show --stat 3cf2e8f2`): 3 files, +215.
- `ops/wq4790-claude-context-sh-into-bin/claude_context.sh` (new, 110 lines).
- `ops/wq4790-claude-context-sh-into-bin/install.sh` (new, 37 lines) — cp-to-tmp-then-
  `mv -f` atomic deploy, `--dry-run` support, idempotent `unchanged:` on re-run.
- `pr_body_wq4790.md` (docs).

The ticket flagged this bundle as missing a `--verify` handler
(`ops/VERIFY_CONTRACT.md`, wq#1696) and required one be added before confirming —
not a re-implementation of wq4790's fix, a mandatory part of this verify archetype.

## Checks performed

1. **Read the shipped `install.sh` cold.** Confirmed no `--verify` case/condition
   anywhere in the file — the bundle had zero liveness-probe coverage
   (`harness/liveness.py:_has_verify_handler` would return `False`, so
   `ops_verified()` returns `UNKNOWN`, and the daily `ops-deploy-drift` sweep only
   reports it heuristically, never alerts on drift).
2. **Added the handler** per the contract template, before any install step:
   `vfile "$SRC/claude_context.sh" "$DST"` + `vdone`, using `ops/_verify.sh`'s
   existing helpers. `DST` already respects `$CLAUDE_CONTEXT_DST` for testing.
3. **Ran the probe** via the actual production codepath
   (`harness/liveness.py:ops_verified`, which internally runs
   `bash install.sh --verify` — the worker Bash allowlist has no entry for running
   `ops/*/install.sh` directly, by design, same restriction wq#4790's own PR body
   hit when it tried to deploy for real). Pointed `FOREMAN_OPS_ROOT` at this
   worktree's copy of the bundle to test pre-merge:
   `('SATISFIED', 'wq4790-claude-context-sh-into-bin: VERIFY OK')`.
   `/home/lumen/bin/claude_context.sh` (live, on this box — this session runs
   directly on Lumen, confirmed via `hostname`) is byte-identical to the bundle's
   shipped `claude_context.sh`.
4. **Falsifiability check (wq#2630 — a probe must be able to fail).** Copied the
   bundle + `_verify.sh` into a scratch dir under `/tmp/fworker-wq4793/`, swapped in
   a one-line tampered `claude_context.sh`, re-ran the same probe against the real
   live `/home/lumen/bin/claude_context.sh`:
   `('STILL_BLOCKED', '... MISS differs: /home/lumen/bin/claude_context.sh ... VERIFY FAIL (1)')`.
   Confirms the handler is a real gate, not a rubber stamp.
5. **Checked whether the live deploy actually happened.** `/home/lumen/bin` is its
   own git repo (lumen-bin); `git -C /home/lumen/bin log --oneline -- claude_context.sh`
   shows HEAD still holding the old 2-line `exec` shim, and `git status` shows the
   live file as locally modified (`M`), uncommitted. This is not a defect: wq#4790's
   own closing notes record the same fact — the real install ran post-merge via
   `lumen-run` (a privileged systemd-run unit, since the BUILD sandbox's
   PRIMARY_TREES wall refuses a direct write to `/home/lumen/bin`), and that path
   writes the file without committing it to lumen-bin's own history. `vfile`'s plain
   `cmp -s` passes directly on the byte match regardless, so this doesn't block the
   new `--verify` handler; flagging it only as a pre-existing, already-disclosed
   loose end (lumen-bin hygiene), not a new finding against this diff.
6. **mcp gitea `pull_request_read`/`get_diff`** hit an unresolved permission prompt
   in this headless session (no human to approve, not on the worker's MCP
   allowlist) and was not retried (wq#1107 loop-breaker). Relied on local git
   history instead, which is authoritative here: `3cf2e8f2` carries `Refs: wq#4790`
   and is already on this worktree's `main`-derived HEAD.

## Verdict: CONFIRMED

wq#4790's diff is a faithful, behavior-preserving relocation — content unchanged,
only its tracked home moved — and the real box already carries the deployed result
(confirmed independently, not just by trusting the closing notes). The one real gap
the ticket called out, the bundle's missing `--verify` handler, is now closed and
proven both to pass on the current live state and to fail on a deliberately tampered
one. No contradiction found; no escalation to opus-5@high warranted.

Shipped as part of this verify-pass: `ops/wq4790-claude-context-sh-into-bin/install.sh`
now has a `--verify` handler (this ticket's own PR).
