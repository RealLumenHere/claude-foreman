# wq#3215 — wire stale-env-audit into rotations + hourly aq alert + docker coverage

Follow-up to wq#3211/wq#3207: `stale-env-audit` (lumen-bin PR#209) can *detect* a rotation
that changed a file but never restarted its consumers, but nothing ran it. This closes the
loop three ways and documents the fourth (false-positive) requirement, which the tool
already implemented.

## What's here

- `stale-env-audit` — the extended tool (source of truth; installed to
  `/home/lumen/bin/stale-env-audit` by `install.sh`). Adds three things to the existing
  systemd-vs-EnvironmentFile audit (`stale-env-audit` / `stale-env-audit.py:1-119` was
  already reading `/proc/<pid>/environ` against each unit's `EnvironmentFiles`):
  - `--docker`: `audit_docker()` (`stale-env-audit:165-192`) discovers each running
    container's compose `env_file` via the `com.docker.compose.*` labels docker itself
    attaches (`compose_env_files()`, `stale-env-audit:125-152` — not a path guess, the
    label is the source of truth for which compose project/service produced the
    container), then diffs `docker exec <c> printenv <VAR>` against it the same way the
    systemd path diffs `/proc/<pid>/environ`.
  - `--raise-aq [--state PATH]`: `raise_aq()` (`stale-env-audit:251-266`) opens one `aq`
    row per drifting unit/container, **keyed on `unit:sorted(differs+missing)`**
    (`row_key()`, `stale-env-audit:231-232`) so a second variable drifting on the same
    unit is a distinct alert rather than being hidden behind an already-open row, and
    `resolve-key`s any previously-open key that's clean this run. `aq add`/`resolve-key`
    dedup on `(source, key)` themselves, so re-running hourly on an unchanged state is a
    no-op — a missed run just catches up on the next tick.
  - `--fail-on-drift NAME[,NAME] --vars VAR[,VAR]`: the rotation-tool hook
    (`fail_on_drift()`, `stale-env-audit:274-276`) — exit 1 if any of NAME is still
    flagged for one of VAR, exit 0 otherwise. It runs the *same* audit as every other
    mode, so "did the restart take" is answered in exactly one place.
- `lumen-stale-env-audit.service` / `.timer` — hourly (`OnCalendar=hourly`,
  `RandomizedDelaySec=120`, `Persistent=true`) run of
  `stale-env-audit --raise-aq --docker`, as root (covers units/containers under other
  uids). This is item (2) from the ticket: a missed restart is now found by the fleet
  within an hour, not by Frank two days later (wq#3211's timeline).
- `install.sh` — deploys the script + units, matches the `ops/wq2152-*` convention
  (`vfile`/`vunit_file`/`venabled`/`vactive` verify contract, `install -m 0755`,
  `systemctl enable --now`).
- `test_stale_env_audit.py` — unit tests for the comparison/dedup/fail-gate logic
  (7 passed, run via `python3 -m pytest -q test_stale_env_audit.py`), plus
  `test_transient_unit_control`, the ticket's TEST TO FAILURE: a real
  `systemd-run -p EnvironmentFile=...` unit taken through drift -> exactly one aq row ->
  restart -> row resolved. Gated on `RUN_LIVE_SYSTEMD_TESTS=1` because it needs root, a
  live systemd, and a live `aq` — none of which this worker's sandbox grants
  (`systemd-run`/`docker ps`/writes to `/etc/systemd/system` and `/home/lumen/bin` are all
  outside this lane's allowlist; see the Deployment note below).

## Item (4) — the false-positive shape (already handled)

The base tool already reports `also_set_inline`: a variable set via systemd
`Environment=`/docker `-e`/`environment:` **and** the file is listed separately from
`differs`/`missing` with the label `also set via Environment= (may be an intentional
override)`. `row_key()`/`fail_on_drift()` only ever look at `differs`+`missing`, so an
inline override can never contribute to an alert key — it's informational by
construction, not filtered after the fact. No further coverage needed here.

## Item (1) — wiring rotation tools

`rotate-provider-keys` (`/home/lumen/bin/rotate-provider-keys`) already has
`consumer_units()`/`restart_consumers()` (added post-wq#3211) but only *warns* on a
restart failure (`run()`, `rotate-provider-keys:193`: `say('WARN: ...')`, exit 0 either
way) — it doesn't confirm the restart actually cleared the drift, and it doesn't fail the
rotation when it didn't. `/home/lumen/bin` is a separate repo outside this branch's reach
(this worktree is `foreman`; writes there are blocked by guard.sh's primary-tree rule, and
`install`/`systemd-run`/`sudo systemctl` are outside this lane's Bash allowlist — confirmed
live, both refused with "requires approval" this session), so the hook is built and tested
here and the four call sites are a mechanical one-line addition for whichever lane next
touches `/home/lumen/bin`:

```python
# after restart_consumers() in rotate-provider-keys' run(), and the equivalent point in
# litellm-provision-keys --rotate and the langfuse/minio/gitea rotate scripts
# (wq#2754/#2756/#2906):
elif not restart_consumers():
    say('WARN: some consumer services not active after restart (keys are swapped and verified)')
changed_vars = [PROVIDERS[p][0] for p in new]           # or that script's equivalent
r = subprocess.run(['sudo', '/home/lumen/bin/stale-env-audit', '--fail-on-drift',
                     ','.join(consumer_units()), '--vars', ','.join(changed_vars)])
if r.returncode:
    say('FAIL: stale-env-audit still sees drift after restart — rotation is not fully live')
    return 4
```

This makes "restart failed to take" a rotation failure (non-zero exit, `say()`'d, callable
from an aq row by whatever dispatches the rotation) instead of a warning nobody reads —
exactly the wq#3211 failure mode: the restart step existed in spirit (four units *should*
have picked up the new key) but nothing checked that it actually happened.

## Deployment

Not deployed live by this worker: `install`, `sudo systemctl daemon-reload/enable`, and
writes under `/etc/systemd/system` or `/home/lumen/bin` are all outside this lane's Bash
allowlist (each refused live with "This command requires approval"), and Edit/Write into
`/home/lumen/bin` is blocked by guard.sh's primary-tree rule. `install.sh` here is written
to the same shape as the merged `ops/wq2152-pgvector-invariants-timer/install.sh`, ready
for whatever merge step deploys that class of ops/ bundle.
