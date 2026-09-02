# wq-worker@.service — one worker per node slot (wq#1182)

Follow-up to wq#1158 (lumen-bin PR#46: atomic claim + fencing tokens for `wq`). That ticket
closed the collision at the *ticket-claim* level: a zombie holding a stale fence can no
longer overwrite a replacement's work. Two pieces were deliberately deferred (see
`migrations/MIGRATION.md` in lumen-bin, "Follow-ups deliberately not in this change"):
Foreman one-worker-per-node systemd, and a `slock` audit. This document covers the first;
the second lands in lumen-bin (`slock`'s own docstring, wq#1182 section).

## The gap this closes

`harness/README.md` ("Build-time discovery vs the design", wq#616) already records that
T2a's original design note — "fworker refuses to start if another fworker-wq* unit is
RUNNING" — was never a real check. wq#616/T2b replaced that placeholder with
`concurrency.py`'s count-then-spawn cap (start 2, hard ceiling 3). A count-then-spawn cap is
a TOCTOU by construction, the same shape wq#1116's README calls out as residual risk at the
wq chokepoint ("no per-instance signal exists"): two racing spawn attempts can each observe
"room for one more" and both launch, putting two `fworker` processes on a node provisioned
for one worker slot at once.

wq's own claim guard still means no ticket is ever double-*worked* — the second process's
`wq claim --id` refuses on the live lease (`dispatcher/DESIGN.md` §9). What it does not
prevent is the wasted process itself: doubled local CPU/model load on a node sized for one
worker, and a doubled claim *attempt* racing wq at the exact moment a lease is expiring. That
is the "double-WORKER" half named in the wq#1158 follow-up review, as distinct from the
"double-claim" half wq#1158 already closed.

## The mechanism

`systemd/wq-worker@.service` is a template unit; `%i` is a node's worker-slot number, not a
ticket id. A node offers a **fixed, small number of slots** (usually one — `wq-worker@1`);
capacity is added by an operator explicitly enabling `@2`, `@3`, ... — this unit never
scales itself, and ships with no `[Install]` section so `systemctl enable` is always an
individual, explicit act per instance (`test_unit_file_has_no_install_section_enabling_a_wildcard`
in `tests/test_wq_worker_slot.py` pins this down).

The flock is what makes "fixed count" a fact instead of a policy, independent of wq, of
`concurrency.py`'s count, and of whatever triggered the start:

- `ExecStartPre=flock -n LOCK -c true` — fails fast, logged, *before* `ExecStart` even runs,
  if slot `%i` is already held. This call acquires-and-releases in the instant `true`
  returns, so on its own it would **not** catch a second start racing in mid-run — it is a
  cheap pre-flight echo of `ExecStart`'s own lock, not the enforcement
  (`test_execstartpre_shape_alone_would_not_catch_a_slow_race` proves this directly, so
  nobody "simplifies" the unit to ExecStartPre-only later and silently drops the guarantee).
- `ExecStart=flock -n LOCK wq_worker_slot.sh %i` — **this** is the enforcement: the lock is
  held by the `ExecStart` process for the run's entire lifetime, so a genuinely concurrent
  second attempt sees it held and is refused outright (exit 1, not queued — `-n`), no matter
  what started it (systemd, a stray manual invocation, a crash-restart race) or when.

Lock files live under `/home/lumen/.locks/wq-worker-<slot>.lock`, matching the fleet's
existing lock-directory convention (`ops/wq980-fleet-config-lock-backup/fleet-safe-write.sh`),
not `/run/lock` — survives the same way the rest of the fleet's advisory locks do.

`harness/wq_worker_slot.sh` is the `ExecStart` body. Locking lives entirely in the unit
file (`flock` wraps the whole script); the wrapper only resolves "what ticket does this slot
run right now" from `EnvironmentFile=-/home/lumen/.locks/wq-worker-<slot>.env`
(`TICKET_ID`/`REPO`/optional `MODEL`/`EFFORT`/`ARCHETYPE`) and execs `fworker.py run` — the
same entry point `dispatcher/foreman_dispatcher.py` already drives via `lumen-run`.

## What this deliberately does not do

Wire `dispatcher/foreman_dispatcher.py` or `harness/spawner.py` to actually target this
template — that means changing the live spawn path (`lumen-run start --name
fworker-wq<ID>` → writing `wq-worker-<slot>.env` and `systemctl start
wq-worker@<slot>`), which is real integration risk this ticket cannot validate against the
live fleet without a maintenance window. The primitive here is deploy-ready and tested in
isolation (`tests/test_wq_worker_slot.py`, zero quota, real `flock` binary, no live
systemd); wiring the dispatcher to it is separate follow-up work, same split as wq#1158's own
"Follow-ups deliberately not in this change".

## Verifying

    cd harness && python3 -m pytest tests/test_wq_worker_slot.py -v

Exercises the real `flock` binary in the exact `-n`/hold-for-lifetime shape the unit file
uses (a second concurrent holder is refused, not queued; the lock frees once its holder
exits) and asserts statically that both `ExecStartPre` and `ExecStart` flock the same
per-`%i` lockfile, so an edit that drops the guard fails the test suite, not just a manual
read of the unit file.
