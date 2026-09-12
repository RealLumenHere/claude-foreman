# wq#1807 — despag P1-4: backing.py Protocols + control_sqlite

PHASE0_WIRING_SPEC.md RECOMMENDATION step 4 + C.4 + E.5. Parallel with the runner
ticket — this module never spawns or runs a harness; it is the seam the runner
ticket plugs into.

## What landed

- `despag/backing/__init__.py` — four `typing.Protocol` interfaces lifted in SHAPE
  (not code) from `research/harnessrouter-ref/src/gateway/backing.py:48-104`:
  `GraphStore`, `BlobStore`, `SecretStore` (+ `SecretsNotConfigured`),
  `WorkspaceFiles`; the `Backing(graph, blob, secrets, mode, workspace)`
  composition class; local implementations `SqliteGraphStore`, `FileBlobStore`,
  `FileSecretStore`, `LocalWorkspaceFiles`; and `make_backing()` reading
  `DESPAG_BACKING` (only `local` is implemented — despag has no cloud-backing
  consumer, unlike HR's `vg` branch) and `DESPAG_DATA_DIR` (default
  `~/despag-data`, created `0700`).
- `despag/backing/control_sqlite.py` — `ControlStore`, mirroring the SEMANTICS of
  HR's `control_sqlite.py` + `control_store.py` idem/lease/resp functions in one
  local-only file (despag has no Cosmos-parity backend to keep byte-identical
  against): `idem_reserve`/`idem_get`/`idem_settle`/`idem_release` (fail-closed,
  atomic `INSERT` via one `BEGIN IMMEDIATE` transaction per op — the loser reads
  the winner's `resp_id`), `lease_admit`/`lease_renew`/`lease_release`/
  `lease_still_held` (best-effort, monotonic fence token, fails OPEN on a store
  error), `resp_put_running`/`resp_mark_terminal`/`resp_get` (one-way terminal
  latch: `cancelled|completed|failed|incomplete`, never revived).
- `despag/app.py` `POST /v1/responses` now honours `Idempotency-Key` (reserve →
  execute-or-replay-or-wait → release-since-the-stub-persists-nothing) and admits
  a `metadata.session_id`/`previous_response_id` through the session lease with
  `reject_on_conflict=True`, returning `409 session_busy` on an active conflict.
  Task execution itself is still the P1-1 `501 lumen_not_implemented` stub — the
  runner ticket fills that in and switches the release to `idem_settle` +
  `resp_mark_terminal`.

## Restic coverage of `~/despag-data`

Could not verify. `/home/lumen/backups` (where the restic include/exclude list
lives) is outside this BUILD worker's bounded filesystem — the harness refuses
any read (`ls`, `cat`) of a path outside the worktree + the
`harness/worker-settings.json` `--add-dir` roots, and `~/backups` is not one of
them. Per the ticket, backup config was **not** edited. Logged as
`kb/ISSUE-LOG.md #360`: a fleet-ops task (run from an unrestricted shell, or add
`~/backups` to a future despag worker's `--add-dir`) is needed to confirm/extend
restic coverage for `~/despag-data` before it holds anything that would hurt to
lose (today: dev-scale SQLite/blob/secret files, no real secrets are written
unless an operator calls `SecretStore.put`).

## Test run

```
$ python3 -m pytest despag/tests/test_backing.py despag/tests/test_control.py despag/tests/test_uhp.py -q
..............................................................           [100%]
62 passed, 1 warning in 0.75s
```

23 tests in `test_backing.py` (Protocol impls: graph merge-not-replace upsert,
label-filtered `get`, edge idempotency, equality/inequality `find`, blob
roundtrip/escape-refusal/prefix-listing, secret env-first/plaintext/encrypted
roundtrip/`require_encryption` refusal/`0600` permissions, workspace
roundtrip/traversal-refusal/sid isolation, `make_backing()` wiring + rejection of
an unsupported `DESPAG_BACKING`), 21 in `test_control.py`, 18 in `test_uhp.py`
(existing P1-1 suite plus the three new route-level idempotency/session_busy
tests below).

Acceptance-critical behaviors, each with a direct test:
- **Two threads racing the same Idempotency-Key produce one reservation** —
  `test_control.py::test_idem_two_threads_race_produce_one_reservation` spawns two
  real OS threads (each its own `ControlStore`/sqlite connection against the same
  file) submitting `idem_reserve` for the same key; asserts exactly one `None`
  (winner) and one non-`None` (loser reading the winner's `resp_id`), then
  re-reads the store from a third connection to confirm only the winner's
  reservation persisted. Ran 8x back-to-back with no flake.
- **Fence mismatch refuses commit** —
  `test_control.py::test_lease_fence_mismatch_refuses_commit`: owner-a admits
  (fence 1), owner-b re-admits (fence 2); owner-a's `lease_still_held(fence=1)`
  flips from `True` to `False` the instant owner-b's admit lands, while owner-b's
  own fence still reads held.
- **Terminal latch cannot go backwards** —
  `test_control.py::test_resp_terminal_latch_cannot_go_backwards`: marks a
  response `completed`, then fires a racing `resp_put_running` (ignored) and a
  second `resp_mark_terminal("cancelled")` (also ignored) — status stays
  `completed` both times.
- **SecretStore refuses plaintext when `require_encryption` and no key** —
  `test_backing.py::test_secret_require_encryption_refuses_without_key` asserts
  `SecretsNotConfigured` is raised; the sibling round-trip test
  (`test_secret_encrypted_roundtrip_with_key`) confirms the on-disk bytes are
  `despagenc1:`-prefixed ciphertext (no plaintext substring) and decrypt back to
  the original value through the same store.
- **`Idempotency-Key` wired into `POST /v1/responses`, 409 `session_busy` via the
  lease** — `test_uhp.py::test_responses_idempotency_key_reservation_is_released_after_the_stub`,
  `test_responses_session_busy_when_lease_is_held_by_another_owner`,
  `test_responses_no_session_id_is_never_session_busy`.

## Dispatcher double-spawn (E.5)

Not wired into `foreman/dispatcher/` in this ticket (out of scope — "design it so
foreman can adopt it later, but do not wire foreman now"). `ControlStore.idem_reserve`
is the reusable primitive: a per-ticket-attempt idempotency key
(`f"{ticket_id}:{attempt}"`) would make a second spawn for the same attempt
impossible by construction, the same way it does here for `Idempotency-Key`.
