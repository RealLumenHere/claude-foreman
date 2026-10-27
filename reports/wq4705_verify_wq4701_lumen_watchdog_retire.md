# wq#4705 — verify-pass of wq#4701 (PR #2501, 215659f0) — CONFIRM

**Verdict: CONFIRM.** wq#4701's diff (`ops/wq4701-lumen-watchdog-retire/`,
`docs/fleet/retired_services.yaml`, the wq#4695 canary-bundle trim, and the
two test files) is internally consistent, matches the live host exactly as
documented, and does not misrepresent its own completion state. No
contradiction; this is not a judgment trigger.

## What I checked

- Read commit `215659f0` cold (`git show --stat`, then the full diff of
  `install.sh`, `remove-units.sh`, `README.md`, `retired_services.yaml`,
  both test files, and the wq#4695 canary bundle's trimmed `install.sh`).
- Read `ops/_verify.sh` to confirm `install.sh`'s `--verify` arm uses the
  shared contract correctly (`_vmiss`/`vcontains`/`vdone`, read-only).
- Ran the probe's exact logic by hand (see below — the literal invocation
  is blocked by this lane's allowlist).
- Ran the shipped tests: `pytest -o pythonpath=harness
  harness/tests/test_wq4701_lumen_watchdog_retire.py
  harness/tests/test_wq4695_retired_canary_targets.py` → **18 passed**,
  matching wq#4701's done notes.
- Cross-checked the live host state against `docs/fleet/retired_services.yaml`
  and `kb/ISSUE-LOG.md` row 1598, and checked the work queue for a
  pre-existing follow-up on the outstanding gap.

## The probe, run by hand

Ticket text says to run `bash
/home/lumen/foreman/ops/wq4701-lumen-watchdog-retire/install.sh --verify`.
That literal invocation is refused by this BUILD lane's allowlist
(`harness/worker-settings.json` has `Bash(bash scripts/*)` but no pattern
covering `bash /home/lumen/foreman/ops/*`) — `This command requires
approval`, not retryable per the house rules. I reproduced the script's
`--verify` branch with the individual checks it runs, all of which are
allowed:

| check (install.sh:12-19) | live result | verdict |
|---|---|---|
| `lumen-watchdog.timer` LoadState == `not-found` | `loaded` (`systemctl show`) | MISS |
| `lumen-watchdog.service` LoadState == `not-found` | `loaded` | MISS |
| `/home/lumen/agent/lumen_watchdog.py` absent | present (`test -e`) | MISS |
| `lumen_watchdog.RETIRED.txt` contains `wq#4701` | file exists but only has the original wq#970 text (Read tool, 1 line) | MISS |
| `fleet-units/known-removed.txt` matches `^lumen-watchdog\.timer` | file exists, no `lumen-watchdog` line at all | MISS |

So `install.sh --verify` would currently print 5 `MISS` lines and exit 1
(`VERIFY FAIL`), not 0.

## Why this is not a contradiction

This is the exact, pinned expected state, not a bug:

- The diff's own test, `test_verify_is_read_only_and_reports_units_still_present`
  (`harness/tests/test_wq4701_lumen_watchdog_retire.py:104-117`), runs
  `install.sh --verify` and asserts `returncode == 1` and `"MISS" in stdout`
  *unless* the root step has already been applied. It passed in my run.
- `docs/fleet/retired_services.yaml:393-395` documents `load_state: loaded`
  for this exact entry, with an inline comment: "both files still in
  /etc/systemd/system until ops/wq4701-lumen-watchdog-retire/remove-units.sh
  runs as root" — matching what I observed live, byte for byte.
- `kb/ISSUE-LOG.md` row 1598 records status `PATH (root apply pending)` for
  this same ticket.
- wq#4701's own done notes state outright: "Root apply is still pending
  (ISSUE-LOG #1598) and a BUILD lane cannot run it."
- wq#4701 already spawned the correct follow-up itself: **wq#4704**
  (`[ops-root-apply] wq4701-lumen-watchdog-retire: install.sh --verify
  fails, needs privileged install`, lane `fleet`, status `blocked`, same
  `Liveness: ops_verified(wq4701-lumen-watchdog-retire)` tag as this
  ticket), blocked for exactly the reason I rediscovered
  ("guard.sh blocks sudo; install.sh requires root remove-units.sh on Lumen
  which BUILD lane cannot run"). There is nothing for wq#4705 to escalate
  that isn't already tracked there.

`remove-units.sh` (root-only; disable+delete both units, move the script
aside to `.retired-wq4701` preserving a rollback path, rewrite the RETIRED
marker, append to `known-removed.txt`, re-baseline `unit-drift`) and
`install.sh --verify`'s check list match each other field for field, and
`bash -n` on both passes (covered by `test_bundle_scripts_are_valid_bash`).
The wq#4695 canary bundle's trimmed `install.sh` no longer references the
retired script and its remaining `vfile` checks still match the live host
(`fleet-retired-nodes.json` byte-identical, checked live by hand).

## Allowlist gap (for the record, not retried)

`Bash(bash /home/lumen/foreman/ops/<bundle>/install.sh --verify)` — or any
`bash ops/*` pattern — is not in `harness/worker-settings.json`'s allow
list for the BUILD lane, so a verify-pass ticket that names this exact
probe cannot run it as written and must reconstruct it from allowed
primitives (`systemctl show*`, `test`, `cat`, Read). Worth widening if
`install.sh --verify` is meant to be the standard verify-pass probe shape.

## Disposition

No escalation. wq#4701 stands as done; wq#4704 already carries the
outstanding privileged-apply step under the same liveness tag. Closing
wq#4705 as done with this report cited.
