# wq#3200 — the `peer` verb: one reviewed narrow exec path to a second node

## Problem

No worker can start ANY process on a second fleet node, by design: `check_run()`
rejects interpreter basenames (`node`, `python`, `bash`, …) via
`_SHELL_ESCAPE_BASENAMES` before any allowlist is consulted, `run_readonly_cmds`
contains nothing that starts a program, thor additionally carries the code-level
`THOR_HARDLOCK`, and thor's lumen-drop has no drop-runner. This blocks wq#484
(M5d cross-node bgblink link needs a Node peer on thor) and every future ticket
whose gate needs a process on a second node. Full evidence: ISSUE-LOG row 1079.

## Options considered (from the ticket)

- **(a) wire the existing `bench` verb** — `check_bench()` already permits a thor
  run for a tool listed in `bench_tools[thor]`. REJECTED as the exec path: a bench
  tool is an argv[0] the worker then passes free argv to. Listing `node` as a
  bench tool re-opens arbitrary interpreter exec (= option (c) scoped to thor);
  refusing interpreters as bench tools means wq#484 only works via a wrapper
  script listed as a tool — which is option (b) with weaker guarantees (PATH-
  resolved basename instead of a pinned absolute path, free worker argv after
  argv[0], no content pin). `bench` stays v2/unwired for its real purpose
  (adb/fastboot hardware benches).
- **(b) a new `peer` verb — CHOSEN.** `fleet-exec peer <node> <name>` executes
  EXACTLY `peer_cmds[<node>][<name>].argv` from fleet-exec-policy.yaml. No worker
  argv reaches the remote node at all.
- **(c) remove node/nodejs from `_SHELL_ESCAPE_BASENAMES`** — REJECTED (per the
  ticket): opens arbitrary exec on every node to beat one game-link ticket.

## Decision: (b), with these invariants (enforced in `check_peer`, lumen-bin)

1. **Fixed argv**: the whole remote argv comes from the PR-reviewed yaml entry;
   the worker supplies only a lookup name. `_SHELL_ESCAPE_BASENAMES` and
   `THOR_HARDLOCK` are untouched — thor stays fully denied except for names
   explicitly reviewed into `peer_cmds[thor]` (the same carve-out shape
   `check_bench` already had).
2. **Content pin**: every entry carries the sha256 of its entry-point file; the
   broker runs `sha256sum` on the node and denies on mismatch BEFORE executing.
   A replaced/tampered entry point never runs.
3. **No interpreter entry points**: `bash`/`node`/`python3`/… as argv[0] are
   denied even if reviewed in — pin a wrapper script so the sha covers what runs.
4. **No worker-writable roots**: an entry point under that node's `copy_dirs`
   (lumen-drop) is denied — otherwise `fleet-exec copy` could overwrite the
   pinned file between review and run (the sha pin would catch it, but the deny
   keeps the review property structural, not incidental).
5. **Fail closed**: malformed entry, missing sha, metachars, relative path,
   non-mapping section — all deny. An installed `fleet_exec_policy` without
   `check_peer` refuses `peer(...)` needs-tags rather than granting unchecked
   (`capability.policy_grants` getattr guard).

## Pieces

- lumen-bin (branch `feat/wq3200-peer-verb`): `check_peer`/`peer_entry` in
  `fleet_exec_policy.py`, `cmd_peer` + parser in `fleet-exec`, policy-matrix +
  CLI tests (sha-mismatch denies before exec; success runs exactly the pinned
  argv).
- foreman (this branch): `peer_cmds: {}` section in
  `harness/fleet-exec-policy.yaml` (empty by design — the verb grants nothing
  until an entry is reviewed in), `peer(<name>@<node>)` needs-tag
  (`harness/needs.py`), grant derivation (`harness/capability.py`
  `policy_grants` → `{"kind": "ssh", "verbs": ["peer"], "peers": [name]}`),
  tests.

## Unblocking wq#484 (follow-on, its own PR)

1. Build the bgblink peer bundle with a `run.sh` wrapper that daemonizes
   (`setsid`, exit 0) — the broker holds the ssh session max `--timeout` (≤3600s).
2. Deploy it to thor OUTSIDE lumen-drop (e.g. `/home/thor/peers/bgblink/`,
   Frank-deployed or via a reviewed ops path — `copy` can only stage into
   lumen-drop, which peer entries may not point into).
3. PR into foreman: add the `peer_cmds.thor.bgblink-peer` entry with the
   deployed file's sha256; add `peer(bgblink-peer@thor)` to wq#484's needs.
