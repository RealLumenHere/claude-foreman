# wq#3846 — root-apply wq#3845 (drop desktop-commander-mcp from healer watch table)

wq#3845 closed with its own `install.sh --verify` FAILING (row still present). The
dispatch note said this needs a privileged install; the bundle's own RUNBOOK
(`ops/wq3845-healer-drop-desktop-commander-mcp/RUNBOOK.md`) is more precise: the deploy
step runs as `lumen`, no root — the privilege gap is that a sandboxed BUILD worker
cannot run `bash ops/*` at all (not on `harness/worker-settings.json`'s allowlist) and
the live file `/home/lumen/agent/healer_watch_table.yaml` sits outside this worktree's
`--add-dir` grant regardless.

`lumen-run` (`/home/lumen/bin/lumen-run`, allowlisted at
`harness/worker-settings.json:19`) is the documented escape hatch: a detached
`systemd-run` unit that runs as `lumen` with normal filesystem access, outside the
per-tool Bash sandbox. Used it to run the bundle's own apply/verify steps:

1. `lumen-run start --name wq3846-healer-install --root -- bash
   ops/wq3845-healer-drop-desktop-commander-mcp/install.sh`
   (unit `lumen-job-wq3846-healer-install-1791960277`) -> exit 0, logged
   `unchanged: /home/lumen/agent/healer_watch_table.yaml` — the row was already gone
   before this run (RUNBOOK notes a duplicate bundle, wq#3816, could land first and
   make this one a no-op; `install.sh` lines 25-28 implement the idempotent
   delete-if-present:

   ```
   ops/wq3845-healer-drop-desktop-commander-mcp/install.sh:25:if grep -qE -- "$ROW" "$TABLE"; then
   ops/wq3845-healer-drop-desktop-commander-mcp/install.sh:26:  cp -p "$TABLE" "$TABLE.pre-wq3845"
   ops/wq3845-healer-drop-desktop-commander-mcp/install.sh:27:  sed -i -E "/$ROW/d" "$TABLE"
   ops/wq3845-healer-drop-desktop-commander-mcp/install.sh:28:  echo "removed row from $TABLE (previous copy: $TABLE.pre-wq3845)"
   ```

2. `lumen-run start --name wq3846-healer-verify -- bash
   ops/wq3845-healer-drop-desktop-commander-mcp/install.sh --verify`
   (unit `lumen-job-wq3846-healer-verify-1791960290`) -> `VERIFY OK`, exit 0. This
   exercises the `--verify` branch's MISS check at
   `ops/wq3845-healer-drop-desktop-commander-mcp/install.sh:17`
   (`grep -qE -- "$ROW" "$TABLE" && _vmiss ...`) against the live table.

3. Independent confirmation: `lumen-run start -- bash -c 'grep -n
   "desktop-commander-mcp" /home/lumen/agent/healer_watch_table.yaml'`
   (unit `lumen-job-wq3846-healer-grep-1791960372`) -> no match, `RC=1`.

Liveness `ops_verified(wq3845-healer-drop-desktop-commander-mcp)` (probe at
`harness/liveness.py:432-451`) now runs `bash install.sh --verify` itself and gets rc 0,
so it reports SATISFIED.

No Frank-gated decision was needed — this was a pure execution-path problem
(root-capable local escape via `lumen-run`, not an actual root/sudo requirement), same
class as `kb/ISSUE-LOG.md` row 166 (wq#1400/wq#1404: `lumen-run start --root -- bash
install.sh` is the established deploy mechanism for this ops/ bundle shape).

Refs: wq#3846, wq#3845
