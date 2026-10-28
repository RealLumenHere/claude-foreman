# wq#4764 — ops-apply gate + node daemons (B2-T1)

Code + tests only. **No install, no live node** — B2-T2 (wq#4767) installs this
via detached `systemd-run` and proves it against a real node; B2-T3
(wq#4769) is the live pilot enable. Design: `docs/agent-authority-research/DESIGN.md`
§3.2-3.6.

The agent never gets root. It submits `{recipe, node, ticket, params}` to a
fixed recipe; a root service that trusts nothing in the manifest
revalidates everything at apply time (V1-V10, `ops_apply/validation.py`)
before anything is staged or applied.

## Layout

- `ops_apply/errors.py` — `OpsApplyError(code, message)`, one of the V1-V10
  refusal codes.
- `ops_apply/canonical.py` — canonical-JSON digest (same `sort_keys` +
  compact-separator convention as `harness/hold_gate.py`'s fingerprint).
- `ops_apply/identity.py` — `SO_PEERCRED` -> cgroup -> worker ticket/unit
  (V1).
- `ops_apply/signing.py` — `ssh-keygen -Y sign`/`-Y verify` wrappers (V8).
  Invocation shape is cited from `man ssh-keygen` (fetched 2026-10-28);
  tests inject a fake `runner` rather than calling the real binary.
- `ops_apply/validation.py` — V1-V10, each a pure function that raises
  `OpsApplyError` on refusal. No I/O; `gate_server.py`/`node_server.py` do
  the I/O and call these in order.
- `ops_apply/queue.py` — one `flock`ed JSON file per request under
  `<queue_dir>/wq<ticket>/<req>.json`; doubles as the V7 approval record.
- `ops_apply/recipes/cloudflared_ingress_upsert.py` — the v1 recipe:
  structural YAML diff validation, `cloudflared ingress validate`/`rule`,
  backup, TOCTOU recheck, atomic rename, restart, post-check, auto-rollback,
  inverse manifest.
- `ops_apply/gate_server.py` — `GateServer` (submit/approve+forward/status/
  revert) plus a thin `AF_UNIX` accept loop.
- `ops_apply/node_server.py` — `NodeServer` (re-validates V3-V10, runs the
  recipe) plus G7 crash recovery (`recover()`) and its own accept loop.
- `cli.py` — the agent-facing client (`ops-apply submit|approve|status|revert`).
- `catalog.json` / `policy.v1.yaml` — the pilot catalog (one recipe, node
  `bes`) and its pinned policy copy; `catalog.json["policy_sha256"]` and the
  recipe's `sha256` are checked against the real files by
  `tests/test_catalog.py` so a drift fails loud instead of going stale.
- `units/*.socket`, `units/*.service` — socket activation + the §3.5
  hardening directives (gate: zero capabilities; node:
  `CAP_CHOWN CAP_FOWNER CAP_DAC_OVERRIDE`).

## Tests

`python3 -m unittest discover -s tests -v` from this directory (or from the
repo root with the same `-s` path). Each V-code has a fixture that makes
exactly that check fail while every earlier check passes
(`tests/test_validation.py`); `tests/test_recipe_cloudflared.py` forces a
post-check failure and asserts the rollback (and rollback-failure/freeze)
path; `tests/test_gate_server.py`/`test_node_server.py` drive the real
`AF_UNIX` accept loop with fakes for subprocess/ssh/wq-lease lookups.
