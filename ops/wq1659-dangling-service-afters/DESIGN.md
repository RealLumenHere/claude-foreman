# wq#1659 — dangling After=litellm.service / lumen-pgvector.service

Source: `research/FLEET_SERVICE_ATLAS.md` #13 (wq#1627): `lumen-fleet-cerebras`,
`lumen-fleet-mistral`, `lumen-fleet-openrouter`, and `lumen-convo-learn` order
`After=`/`Wants=litellm.service`; `lumen-research-index` orders
`After=lumen-pgvector.service`. Both `litellm` and `lumen-pgvector` are docker
containers — systemd has never loaded either name, so the ordering is a no-op
today (`FLEET_SERVICE_ATLAS.md` :68: "`Wants=litellm.service` on the
researchers is a no-op; their real gate is the `docker.service` Requires plus
the container's own `restart=unless-stopped`"). Benign now, but the unit files
claim a boot-ordering guarantee they don't provide, and nothing catches the
next one of these.

## Fix

1. **Per-unit**: drop the dangling `After=`/`Wants=` line, order
   `After=docker.service` (+ `Requires=docker.service` where missing), and add
   an `ExecStartPre=/home/lumen/bin/wait-for-port <host> <port> <timeout>` gate
   so the unit still waits for the *actual* dependency — the container's
   published port — instead of a fictional unit. See `units.diff` and
   `wait-for-port`.
2. **General**: `unit-drift check` (wq#1041) already walks every tracked unit
   file; `staged/unit-drift` adds `dangling_target_lint()`, which parses every
   `After=`/`Wants=`/`Requires=`/`BindsTo=`/`PartOf=` line in every unit under
   `/etc/systemd/system` and flags any target with no loadable unit anywhere
   in `SEARCH_PATHS` (`/etc|/run|/usr/lib|/lib`/systemd/system — the same set
   `base_unit_exists()` already used for orphaned drop-ins). A violation now
   counts as drift, so it fails `unit-drift check` and shows up in the
   `--digest` ntfy exactly like an added/removed/modified unit. This is the
   generic guard the ticket asks for: the next unit file that orders after a
   container's fake unit name fails CI-equivalent (`unit-drift check`) instead
   of sitting silent for months.

## Why `wait-for-port` and not `curl`/`pg_isready`

Both target ports are TCP-listen checks, not protocol-aware health checks
(litellm's `/health` and postgres's actual readiness are a stronger guarantee,
but pulling in `curl`/`postgresql-client` as a hard dependency for a generic
`ExecStartPre` helper is unnecessary — bash's `/dev/tcp` pseudo-device needs
no extra package and is already relied on elsewhere in this fleet's shell
tooling). A protocol-aware check can replace the TCP wait per-unit later
without touching the lint.

## Test

`tests/test_dangling_target_lint.py` loads `staged/unit-drift` the same way
wq#1657's `test_unit_drift_wiring.py` does (exec into a fresh module, patch
`SYS` to a fixture dir) and asserts:
- a unit with `After=litellm.service` (no such unit anywhere in
  `SEARCH_PATHS`) is flagged;
- a unit with `After=docker.service` (present on any real system, faked here
  by dropping a `docker.service` file into the fixture's `SEARCH_PATHS`
  override) is clean;
- a unit with only `%`-specifier targets (e.g. `OnFailure=ntfy-onfailure@%n.service`
  parsed incidentally by a broader grep) is skipped rather than false-flagged.
