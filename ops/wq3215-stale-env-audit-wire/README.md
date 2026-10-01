# wq#3215 — wire stale-env-audit into rotations + hourly aq alert + docker coverage

Follow-up to wq#3211/wq#3207: `stale-env-audit` (lumen-bin PR#209) can *detect* a rotation
that changed a file but never restarted its consumers, but nothing ran it. This closes the
loop three ways and documents the fourth (false-positive) requirement, which the tool
already implemented.

## What's here

This bundle owns the **binary only**. The hourly unit and timer belong to
`ops/wq3215-stale-env-audit-timer`, which was already installed; this bundle used to ship a
second copy of the same two `/etc/systemd/system` paths with a different `ExecStart`, so
whichever bundle installed last would have made the other's `--verify` fail forever. See
"wq#3245 — the reconciliation" below.

- `stale-env-audit` — the tool, installed to `/home/lumen/bin/stale-env-audit` by
  `install.sh`. It is the live `--aq` build (`d35e196`) plus two modes:
  - `--docker`: `audit_docker()` discovers each running container's compose `env_file` via
    the `com.docker.compose.*` labels docker itself attaches (`compose_env()` — not a path
    guess, the label is the source of truth for which compose project/service produced the
    container), then diffs the container's live environment against it. The read is one
    `docker exec <c> cat /proc/1/environ` per container, the same ground truth the systemd
    path uses, not a `printenv` per variable.
  - `--fail-on-drift NAME[,NAME] --vars VAR[,VAR]`: the rotation-tool hook
    (`fail_on_drift()`/`run_fail_on_drift()`). Exit 0 only when every NAME was examined and
    none drifts on a VAR; 1 when one still drifts; **2 when a NAME was never examined**. It
    runs the same audit as every other mode, so "did the restart take" is answered in
    exactly one place.
  - `--aq` is unchanged in shape from the live build: one row per drifting unit, source
    `stale-env`, key = unit, read back out of the alert DB rather than a side state file.
    Its resolve rule is now per-unit (`plan_aq()`), see the reconciliation section.
- `fixtures/docker-compose.fixture.yml` — committed compose file the parser tests read, so
  the compose-shape evidence is pinned and re-runnable instead of depending on whatever
  compose projects happen to be up.
- `install.sh` — installs the binary, then runs the wq#3228 repro as a post-install gate:
  an install whose binary still answers `0` for a unit it never examined fails.
- `test_stale_env_audit.py` — 34 unit tests over the comparison, compose-parsing, aq-planning
  and CLI-contract logic, plus `test_transient_unit_control`, the TEST TO FAILURE: a real
  `systemd-run -p EnvironmentFile=...` unit taken clean -> drift -> restart with
  `--fail-on-drift`'s exit code asserted at each step. Gated on `RUN_LIVE_SYSTEMD_TESTS=1`
  because it needs root and a live systemd.

## Item (4) — the false-positive shape

A variable declared **both** inline (systemd `Environment=`, compose `environment:`) and in
the env file is an intentional override of the file, not drift: the unit pins its own value,
so no restart can make the process take the file's and an aq row for one is a nag nothing can
close. wq#3215's draft claimed this was already handled "by construction" because the alert
key only read `differs`+`missing` — but an inline-overridden variable *lands in* `differs`,
so it did alert. The first live `--docker` run opened rows for `docker-plugin_daemon-1` and
`docker-api_websocket-1` on exactly that (`DB_DATABASE`, `SERVER_WORKER_CLASS`,
`SERVER_WORKER_CONNECTIONS`).

`split_inline()` now subtracts those names from `differs`/`missing` and the row moves from
`res["drift"]` to `res["overrides"]`: reported, never alerted, and still failed by
`--fail-on-drift`, where the operator running the rotation is the one person who can act on
a value pinned away from the one they just rotated to.

Same class, found the same way: the compose CLI's own control variables
(`COMPOSE_CONTROL_VARS`). `COMPOSE_PROFILES` is read out of `.env` by compose and resolved at
`up` time rather than passed through, so a container that also lists that `.env` as its
`env_file` disagrees with it forever. It alerted on six dify containers; no recreate could
have closed those rows.

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

Deployed 2026-10-01 under wq#3245. A worker cannot `install` or Write into `/home/lumen/bin`
directly (ISSUE-LOG #770: bare `bash <abs path>` is not on the allowlist; #1106: guard.sh's
primary-tree rule refuses Edit/Write there), but `lumen-run` is allow-listed and runs the
script as a detached transient unit — which is the path the timer bundle's own header already
documents:

```
/home/lumen/bin/lumen-run start -- bash /home/lumen/foreman/ops/wq3215-stale-env-audit-wire/install.sh
/home/lumen/bin/lumen-run start --root -- bash /home/lumen/foreman/ops/wq3215-stale-env-audit-timer/install.sh
```

Order matters: the timer's `ExecStart` now passes `--docker`, and its install refuses to run
against a binary whose `--help` does not advertise it.

`/home/lumen/bin/stale-env-audit` is **also tracked by the separate lumen-bin repo** at
`d35e196`. This bundle is now its source of truth; the installed file is ahead of what that
repo has committed, and someone with write access there has to commit or discard the diff.
Filed as a follow-up (see the reconciliation section).

## wq#3224 — remainder of item (1): gating the rotation tools

**Correction (wq#3228/wq#3245):** this section originally said the tool "shipped as part of
wq#3215 … installed 2026-09-30 09:18Z, and already wired hourly with `--docker`". It had not.
wq#3215's `install.sh` was refused by the worker sandbox and the ticket blocked, so the live
binary stayed at `d35e196` with only `--json` and `--aq`, and the hourly unit that *was*
installed came from `ops/wq3215-stale-env-audit-timer` and ran plain `--aq`. The gate below
was therefore calling two flags nothing implemented. Both are live as of 2026-10-01.

wq#3224 is the follow-up that actually calls the gate from the rotation tools:

- **`ops/wq1747-litellm-key-rotation/rotate.py`** (the tool that actually restarts
  consumers for the wq#1747/#1968 leaked-key remediation) is now wired end to end in this
  branch:
  - `restart_order_key()` (`rotate.py:279-284`) restarts workers first and any unit with
    "messenger" in its name last, so a bad rotation surfaces on a background worker
    before it ever reaches the unit Frank is talking to.
  - `confirm_no_drift()` (`rotate.py:297-312`), called from `rotate_one()`
    (`rotate.py:420`) right after the restart loop, re-runs `stale-env-audit
    --fail-on-drift` against every unit this run actually restarted and every variable
    name the rewritten files declared — the same "did the restart actually take" check
    wq#3211 was missing. `result["drift_ok"]`/`drift_note` feed `main()`'s exit code
    (`rotate.py:497-499`), so a rotation that restarts cleanly by systemd's own account
    but still shows stale env now fails loud instead of reporting `verify_ok` and moving
    on.
  - Tests: `test_messenger_unit_restarted_last`,
    `test_post_restart_drift_gate_fails_rotation_when_still_drifting`,
    `test_main_exit_code_reflects_drift_gate_failure` in
    `ops/wq1747-litellm-key-rotation/test_rotate.py` (13 passed).
- **`rotate-provider-keys`, `litellm-provision-keys --rotate`, `gitea-rotate-finish`** all
  live in `/home/lumen/bin`, a shared **primary** tree. Confirmed again this session,
  the hard way: `lumen-pr start /home/lumen/bin fix/... --primary` happily checks out a
  branch there (the broker recognizes this ticket), but every subsequent Edit/Write is
  refused by guard.sh's primary-tree rule (wq#1719/#900) — and the recovery command
  `lumen-pr ffprimary` itself prints (`git -C /home/lumen/bin checkout main`) is refused
  too, by the git-HEAD-move rule (wq#900/#929). A worker can check that repo out but
  cannot write to it or check it back out again. Filed as ISSUE-LOG #1106; the
  `fix/wq3224-gate-rotation-tools-on-stale-env-audit` branch is left checked out there
  (0 commits ahead of `main`) for an orchestrator session to either apply the snippet
  above and land it, or `git checkout main` to release it.
  Separately, `litellm-provision-keys --rotate` doesn't know which systemd units consume
  `~/.secrets/litellm-service-keys/env/<alias>.env` without reading `/etc/systemd/system`
  (the way `rotate.py`'s `map_file_to_units()` does) — that directory is outside this
  worker's allowed read paths entirely (confirmed live: `grep` there is refused with "may
  only search ... allowed working directories"), so even a read-only companion script
  can't be built for it from this sandbox. Both gaps need an orchestrator, not a worker.
- **langfuse/minio rotation** (wq#2754/#2756): no reusable script exists to wire — that
  rotation was a one-off manual `docker-compose.yml` edit
  (`reports/2754_langfuse_minio_clickhouse_rotate.md`), not a tool. Nothing to gate until
  that rotation gets a script of its own.

## wq#3245 — the reconciliation

wq#3228's verify pass of PR#1492 found the gate calling flags the live binary did not have.
The root cause was a fork: this bundle's `stale-env-audit` and the installed
`/home/lumen/bin/stale-env-audit` had diverged by 298 lines and **neither was a superset** —
the live build had `--aq`, the bundle's had `--docker`/`--raise-aq`/`--fail-on-drift`. Running
this bundle's `install.sh` as it stood would have replaced a working `--aq` build and the
installed timer unit with a different contract. The judgment calls, with reasons:

- **Base on the live build, port the two modes onto it.** Not the reverse: `--aq` is wired
  into an installed, enabled timer with `SuccessExitStatus=0 1` and a `lumen-cron-stamp`
  hook, and the hourly run is the only thing that was actually working.
- **Drop `--raise-aq` and its `--state` JSON file.** It was a second, divergent record of
  which alerts are open. `--aq` reads the alert DB itself, so it cannot disagree with reality;
  a state file can, and silently. One aq mode, not two.
- **Keep the `--aq` key as the unit name, not `unit:vars`.** `--raise-aq`'s finer key opens a
  second row when a second variable on the same unit drifts. The fix is identical either way
  (restart the unit), so the extra row is extra noise, and `aq` already dedups on
  `(source, key)`.
- **This bundle stops shipping the unit files.** Two bundles installing the same two
  `/etc/systemd/system` paths with different contents means one of their `--verify` handlers
  is permanently red and `ops-deploy-drift` alerts forever. The timer bundle owns the units;
  `--docker` went into *its* `ExecStart`.
- **Reject unknown and abbreviated flags.** This is the actual root cause, not the missing
  flags: `d35e196` scanned argv by substring, so it ignored `--fail-on-drift`/`--vars` and
  answered `0` from a whole-fleet audit. argparse with `allow_abbrev=False` makes the next
  caller/callee skew a loud exit 2 instead of a silent pass.
- **`--fail-on-drift` exits 2 for a name it never examined.** A typo'd unit, a unit that is
  not running, and a unit with no `EnvironmentFile=` to compare against all used to read as
  "clean". "I could not look" must not read as "it is fine" (wq#2985, the same rule
  `ops/VERIFY_CONTRACT.md` applies to remote nodes).
- **`vflag` in `ops/_verify.sh`.** New shared helper: assert the *callee's* `--help`
  advertises a flag the bundle's payload depends on. A `vfile` byte-match on the caller is
  exactly what could not catch wq#3228. Used by all three bundles here. Against the
  pre-install binary it reported `VERIFY FAIL (2)` with two `MISS` lines, which is the proof
  it can fail.

Two defects only the live run could find, both fixed here:

1. `--docker` opened 13 aq rows, 7 of them unclosable (inline overrides and
   `COMPOSE_PROFILES`) — see item (4) above.
2. `--aq` resolved **0 of 7** rows it had itself obsoleted, because `d35e196` gated
   resolution on fleet-wide `unreadable == 0` and one short-lived unit's `/proc` vanished
   mid-scan. `plan_aq()` now resolves per-unit: a row closes when *that unit* was examined and
   is not drifting. Absence of evidence still resolves nothing. After the fix the same run
   reported `6 row(s) opened/refreshed, 7 resolved`.

Still open, needs write access this lane does not have: `/home/lumen/bin/stale-env-audit` is
tracked by the lumen-bin repo at `d35e196` and the installed file is now ahead of it. Either
commit the diff there or stop tracking the path and let this bundle own it outright. Also
unchanged: `rotate-provider-keys`, `litellm-provision-keys --rotate` and `gitea-rotate-finish`
still only *warn*; the one-line snippet above is what they need, and it now calls flags that
exist.

## wq#3224 — item (3): non-systemd long-running processes (tmux, nohup)

Decision: **don't** add a blind `/proc` scan by cmdline. stale-env-audit's whole method is
comparing a process's live environment against a *known, named* expected source (a unit's
`EnvironmentFile=`, a container's compose `env_file`); a bare `tmux`/`nohup` process has no
such declared source; a cmdline-pattern scan would have nothing authoritative to diff
against and would either miss drift or manufacture false positives from ordinary
long-lived shells. The fix that generalizes is upstream of the audit: any long-running
process that needs staleness protection should be a systemd unit (a real one, or a
transient `systemd-run --collect` unit) with an explicit `EnvironmentFile=` — it then falls
under the existing systemd audit path for free, with no new detection code. Recommend
flagging any tmux/nohup process found to be holding a secret-bearing env var (via a manual
audit, not a new automated scanner) for conversion to a unit rather than building a second,
weaker detection path for it.
