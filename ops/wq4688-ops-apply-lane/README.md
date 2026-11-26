# wq#4688 — ops-apply lane: permission profile, spawn gates, broker verbs

Design: `design/orchestrator_verify_lane.md` §3 option B + §6 (gate table) + §7 (exit
criterion for B); `docs/agent-authority-research/DESIGN.md` §3.7 (this lane is the
non-root half; the wq#4764 daemon stays the only root path).

**Code + tests only. Nothing here is installed on any node.** The foreman half lands
with this PR but is inert: the lane is `enabled:false` in `dispatcher/config.json` (never
scanned), and gates 4/5 refuse every spawn until the lumen-bin half below is installed.

## What landed in foreman (this PR)

| file | role |
|---|---|
| `harness/ops_apply_lane.py` | gates 0-5, judge binding, grant entries, orchestrator CLI (`backup`, `binding`, `check`) |
| `harness/permission-profiles/ops-apply.json` | the lane's whole permission pack (selected by lane, wins over archetype/repo) |
| `harness/fworker.py` | `assert_ops_apply_gates()` at the top of every `do_attempt`; lane-routed settings; pinned grant merged into `grant.json`; manifest stripped before the cited-path wall; fire-mode refused |
| `harness/fleet-exec-policy.yaml` | NEW `allow.apply_staged` + `allow.docker_exec` only; `never.*` byte-identical |
| `dispatcher/config.json` | `ops-apply: {enabled: false, manual: true}` — so `wq edit --lane ops-apply` is accepted (wq#912) and the timer never sees it |
| `harness/tests/test_ops_apply_lane.py` | T5 (real `fworker run`), auto-rollback, per-gate cases, pack/config/policy shape |

## What is staged here for lumen-bin (NOT installed)

`lumen-bin.patch` (base: `lumen-bin.base`, lumen-bin `8bfd9df`) adds:

- `fleet_exec_policy.py`: `check_apply_staged` (exact tree in `allow.apply_staged[node]`,
  Lumen-local v1, never.nodes/THOR_HARDLOCK first) and `check_docker_exec` (whole-token
  `(container, argv-prefix)` pairs, shape check, no `-c`, no interpreters).
- `ops_apply_staged.py`: patch-shape rules (plain text modify/create/delete only), the
  backup format, `verify_backup` (backup must equal the CURRENT live state — stronger than
  the design's "newer than mtime"), `restore_backup`, GET-only / readonly-argv post-check.
- `fleet-exec`: verbs `apply-staged <node> <tree>` and
  `docker-exec-allowlisted <node> <container> -- <argv>`. Every operand comes from the
  pinned grant entry. apply-staged: patch re-hashed, backup re-verified, applied from a
  private 0600 copy after `git apply --check`, unit restarted via the broker's own
  `restart` verb, post-check polled 30 s; mismatch => restore backup, re-restart,
  `wq block` (exit 3; exit 4 + urgent page if the restore does not verify).
  docker-exec: exact pinned argv; mismatch => pinned inverse argv + `wq block`.
- `tests/test_fleet_exec_ops_apply.py` (62 tests).

Install (separate ticket, Frank/orchestrator, NOT this one):
`cd /home/lumen/bin && git apply --check <this>/lumen-bin.patch && git apply …`, via a
lumen-bin PR, then `python3 -m pytest tests/test_fleet_exec_*.py`. The harness test
`test_ops_apply_lane.py::patched_bin` fails loudly if live lumen-bin drifts from the base.

## Orchestrator runbook for one ops-apply ticket

1. Stage the patch somewhere stable (e.g. `/home/lumen/foreman/_runs/wq<N>/ops-apply/fix.patch`).
2. `python3 /home/lumen/foreman/harness/ops_apply_lane.py backup --tree <tree> --patch <p> --out /home/lumen/backups/ops-apply/wq<N>-<ts>`
3. Put one manifest block in the detail (`wq edit <N> --detail …`):
   ~~~
   ```ops-apply
   node: lumen
   tree: /home/lumen/messenger
   patch: /home/lumen/foreman/_runs/wq<N>/ops-apply/fix.patch
   patch_sha256: <sha256sum of the patch>
   backup: /home/lumen/backups/ops-apply/wq<N>-<ts>
   restart: lumen-messenger
   postcheck_http: http://100.117.66.102:8765/wq
   postcheck_expect: "detail"
   ```
   ~~~
   (docker form: `docker_container`, `docker_argv`, `docker_inverse_argv`, `backup`,
   `backup_of`, `postcheck_*`.)
4. Verify report naming the patch sha256 and unit -> `wq edit <N> --gate /abs/report.md`.
5. `python3 …/ops_apply_lane.py binding --ticket <N>` -> run the printed `judge` command;
   verdict must be `allow`.
6. `python3 /home/lumen/minerva/lumen_episode.py precheck "<plan>"` -> `wq note <N> "<output>"`.
7. `wq edit <N> --lane ops-apply`; `python3 …/ops_apply_lane.py check --ticket <N>` must say PASS.
8. Spawn by hand (`python3 /home/lumen/foreman/harness/fworker.py run --ticket <N> --repo <repo> …`).
   Never via the dispatcher.

## Rollback

- Lane: delete the `ops-apply` entry from `dispatcher/config.json` (wq then refuses the
  lane) and/or delete `harness/permission-profiles/ops-apply.json` (every spawn refuses
  `OPS_APPLY_PROFILE_MISSING` before any gate).
- Policy: delete `allow.apply_staged` / `allow.docker_exec` -> both verbs deny.
- lumen-bin (once installed): `git revert` the lumen-bin PR.
