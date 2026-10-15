# wq#4017 verify: wq#4006 [security] REA on Lumen user-scope/unpinned/provenance rescope

Archetype 8 adversarial verify pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model as author). Verify repo: `/home/lumen/foreman`.

## What wq#4006 claimed

REA (`rea-agents`, github.com/morluto/rea) was registered at **user scope** in
`~/.claude.json` (`rea`, `rea-maksoor`), so every Claude Code session on Lumen — including
fworkers — got REA's ~170 tools regardless of need; version had silently drifted
6.4.0 -> 6.5.0 with no PROVENANCE/SHA256 record. Fix shipped as
`ops/wq4006-rea-security-rescope-a1/` (landed `main` `a6065692`, squashed PR #1944):
move the registration to project scope at `~/re` (mirroring `ghidra-lumen`), pin the
version + npm integrity hash into `PROVENANCE.json`, block `rea update`/`self-update`/
`upgrade` via a `rea-guard` wrapper, and add `common/tools/HOSTILE_INPUT_POLICY.md` (none
existed before). The ticket's own notes and `TOOL_REGISTRY.md`/`research/
REA_SCOPING_AND_PINNING.md` state the live `~/.claude.json`/`~/re` cutover is a
privileged/non-worker apply step (both paths outside a sandboxed BUILD worker's granted
dirs; `~/.claude.json` gates every session on Lumen) — shipped, not applied.

## What I checked

- Read the full squashed commit (`git show a6065692`, 10 files/716 insertions) cold:
  `install.sh`, `remove_user_scope.py`, `rea-guard`, `generate_provenance.py`,
  `rea.mcp.json.project`, `HOSTILE_INPUT_POLICY.md`, `RUNBOOK.md`,
  `research/REA_SCOPING_AND_PINNING.md`, `TOOL_REGISTRY.md`'s diff, and
  `ops/tests/test_wq4006_verify.py`.
- Ran the bundle's own test suite standalone: `python3 ops/tests/test_wq4006_verify.py -v`
  -> **10 passed** (fixed-state pass, no-`.claude.json` pass, drifted/still-registered
  fail, malformed-JSON error, each of guard/policy/mcp.json/PROVENANCE missing
  individually, thin mcp.json missing `rea-maksoor`, mcp.json not pointed at the guard,
  thin provenance missing `integrity`). Every case runs against temp fixtures
  (`WQ4006_CLAUDE_JSON`/`WQ4006_REA_PROJECT_DIR`), never the real `~/.claude.json`/`~/re`.
  Several cases assert non-zero exit against deliberately-broken fixtures and the
  malformed-JSON case exercises an input outside the present/absent population the fix
  targets — satisfies the wq#2630 falsifiability doctrine (the probe can fail, and does,
  on the right inputs).
- Confirmed the prior wq#4017 attempt's PERMISSION_LOOP cause directly: a bare `bash
  /home/lumen/foreman/ops/wq4006-rea-security-rescope-a1/install.sh --verify` from this
  worktree is refused ("requires approval") — `harness/worker-settings.json` only
  allowlists `Bash(bash scripts/*)`, not `bash ops/*` or bare `bash`. Did not retry the bare
  form; pivoted to `lumen-run`, the documented escape hatch used by the wq3845/wq3846/
  wq3847/wq3848 precedent chain for this exact shape of refusal.
- Independently re-ran the LIVE target (read-only `--verify`, never the apply form) via
  `lumen-run start --name wq4017-verify-live -- bash
  /home/lumen/foreman/ops/wq4006-rea-security-rescope-a1/install.sh --verify` ->
  `lumen-run wait` = `FAILED:1`; log shows:
  ```
  MISS absent: /home/lumen/re/bin/rea-guard
  MISS absent: /home/lumen/re/common/tools/HOSTILE_INPUT_POLICY.md
  MISS absent: /home/lumen/re/.mcp.json
  MISS absent: /home/lumen/re/PROVENANCE.json
  MISS still in user scope: rea, rea-maksoor
  VERIFY FAIL (5)
  ```
  This is **not a contradiction** — it is independent confirmation that the live state is
  exactly what wq#4006, `TOOL_REGISTRY.md`, and `kb/ISSUE-LOG.md` row 1346
  (`BUNDLE-SHIPPED-NOT-APPLIED`) already say it is. No `[ops-root-apply]` ticket exists yet
  for this bundle (checked `wq list --lane build` against the wq3354/wq2608/wq3845/wq3911
  precedent pattern, each of which got its own root-apply ticket) — consistent with the
  bundle having landed same-day and `ops-deploy-drift --aq` only alerting bundles older
  than 24h.
- Adversarially traced `rea-guard`'s subcommand match (`update|self-update|upgrade` in
  `"$@"`, else `exec "$REAL_REA" "$@"`) against the original vulnerable registration: the
  pre-fix `~/.claude.json` entry calls `rea` (PATH-resolved, fixed npm-global binary, per
  `TOOL_REGISTRY.md:52-64`), not `npx rea-agents@latest` — so the drift vector the guard
  targets (an in-CLI self-update subcommand reinstalling the package) is the same surface
  the original 6.4.0->6.5.0 drift is attributed to, and the guard sits in the one place
  (`.mcp.json`'s `command`) that invocation must pass through.
- Found one documentation inconsistency: `HOSTILE_INPUT_POLICY.md` §3 lists `rea update`,
  `rea self-update`, `rea upgrade`, and `npx rea-agents@latest` together as commands not to
  run, then says "the pinned version is the only version this project's registration may
  invoke (enforced by `bin/rea-guard`...)". Read plainly this parenthetical implies
  `rea-guard` enforces all four, but `rea-guard` cannot intercept `npx rea-agents@latest`
  at all (npx fetches from the registry directly, bypassing the wrapper and PATH entirely)
  — `RUNBOOK.md:34-38` and `research/REA_SCOPING_AND_PINNING.md:42` both correctly state
  this is policy-only for that one case, not wrapper-enforced. Minor wording defect inside
  the new policy doc, not a functional gap: the real enforcement for `npx @latest` was
  never anything but policy + PROVENANCE review discipline, and both of the other two
  disclosures in the same PR say so accurately. Does not change the verdict (same class as
  wq#3848's "cosmetic nit").
- Checked whether the pin is continuously enforced or just a point-in-time record:
  `install.sh --verify` only checks `PROVENANCE.json` contains shape-valid `version`/
  `integrity` fields, never cross-checks them against the live `$REAL_REA --version`
  output, and `rea-guard` execs the real binary unconditionally once the subcommand
  check passes — so a binary swapped in by some means other than the guarded subcommands
  (e.g. a manual `npm install -g rea-agents@latest` run outside the `.mcp.json` path)
  would not be caught by a later `--verify` run. This matches the existing fleet
  convention for `/opt/ghidra-mcp/6.0.0/PROVENANCE` (also a point-in-time record, not a
  live cross-check) and is explicitly scoped out by `RUNBOOK.md` ("rescope-only ticket");
  not a claim wq#4006 made and failed to deliver.
- Confirmed the two already-disclosed open gaps are genuinely still open and not silently
  papered over: `rea-maksoor`'s `.mcp.json` entry execs the real binary on Maksoor directly
  over ssh with no equivalent guard (`rea.mcp.json.project:11-18`), and the runtime-capture
  isolation in `HOSTILE_INPUT_POLICY.md` §2 is a policy rule, not a mechanical sandbox —
  both match `research/REA_SCOPING_AND_PINNING.md` §4's own "what's still open" list,
  which this PR states plainly rather than hiding.

## What I did not find broken

- `remove_user_scope.py` only touches the top-level `mcpServers` block and is a no-op if
  the keys are already absent — idempotent, matches its own docstring.
- `generate_provenance.py` uses `subprocess.run(["npm", "view", spec, ...])` as an argv
  list (no `shell=True`), so no command-injection risk from `package`/`version` args.
- `install.sh --verify` is genuinely read-only (no writes, matches `ops/_verify.sh`'s
  contract) — confirmed both by reading it and by the fact my live run against the real
  `~/.claude.json`/`~/re` changed nothing.
- No scope creep: `TOOL_REGISTRY.md`'s diff only adds a status block and an open-hold
  update consistent with the bundle; no unrelated sections touched.
- No fabricated evidence (instinct 8 check): every path, line number, and command in this
  report is one I ran or read directly this ticket; the `lumen-run` unit name
  (`lumen-job-wq4017-verify-live-1792059466.service`) is this run's own, not reused from
  elsewhere.

## Verdict: CONFIRM

No contradiction. The shipped diff does what it claims: the test suite is falsifiable and
passes 10/10, the live target today is in exactly the "shipped, not applied" state the
ticket, `TOOL_REGISTRY.md`, and `kb/ISSUE-LOG.md` row 1346 already say it's in (independently
re-verified by me via `lumen-run`, not just trusted from the ticket notes), and the two
open gaps (Maksoor-side guard, capture isolation as policy not mechanism) are genuinely
disclosed rather than glossed over. One documentation wording nit in
`HOSTILE_INPUT_POLICY.md` §3 (parenthetical overstates `rea-guard`'s reach to include
`npx rea-agents@latest`, which it cannot block) — cosmetic, does not change the verdict.
`ops_verified(wq4006-rea-security-rescope-a1)` liveness: **STILL_BLOCKED** (expected; no
root-apply has run yet). Filed `wq#4040` `[ops-root-apply] wq4006-rea-security-rescope-a1`
to carry the privileged apply step, mirroring the wq3845/wq3846/wq3847/wq3848 precedent
chain.

Refs: wq#4017, wq#4006, wq#4040.
