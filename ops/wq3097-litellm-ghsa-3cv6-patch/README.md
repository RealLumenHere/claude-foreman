# wq#3097 — LiteLLM GHSA-3cv6-jpf6-8222 (CVE-2026-84377, Moderate) patch prep

Gap G2 from `research/wq1402_upgrade_batch_manifest.md`: GHSA-3cv6-jpf6-8222 (authenticated SSRF +
provider-credential exfiltration) is not fixed anywhere on the 1.83 line Lumen runs. Fixed versions:
v1.88.6 (`sha256:0c73da8d132b893bfa037511553bafbc9e2ff6abe9b474a66e0e9a354c0be064`, fallback) and
v1.96.2 (`sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63`, target). Upstream
fix: https://github.com/BerriAI/litellm/pull/36011 (`reject_url_valued_destination`, defined in
`litellm/proxy/litellm_pre_call_utils.py`).

This is the 4th build attempt on this ticket (a1-a3 were KILLED — a1 hit the primary-tree write
freeze on `/home/lumen/bin` and left patches unapplied; a2/a3 got past that via `lumen-pr start
--worktree` into a linked worktree, wrote and committed the actual fix there as PR #187, but the
attempt itself was killed before landing, apparently after `lumen-window`/`gh`/docker-apply calls
were refused — see `/home/lumen/foreman/_runs/wq3097/failure.json`, `last_error_sig: docker denied
except ps/logs`).

## Status vs. the ticket's "TODO before queueing" gate

| item | status |
|---|---|
| (a) `litellm-upgrade --verify` asserts `reject_url_valued_destination` fix marker | **done, landed** |
| (b) `harness_litellm.sh` OLD/NEW/fix-marker parameterized, forced-verify-failure rollback path added | **code landed**; actual re-run to failure **not executed** — see below |
| (c) caller grep for the 3 PR 36011 breaking shapes | **done** — 0/3 shapes hit, re-run fresh this attempt (2026-09-29) |
| (d) migration-aware rollback design + post-rollback smoke re-check | **done, landed** (see note on scope below) |

Items (a), (b)'s code, and (d) were written and committed by a prior attempt (a2/a3) directly in
`lumen/lumen-bin`'s linked worktree (branch `fix/wq3097-litellm-3cv6-window-op`, PR #187), but that
PR was never landed before the attempt was killed. This attempt (a4) landed it:

```
lumen-pr land /home/lumen/scratch/worktrees/bin-fix-wq3097-litellm-3cv6-window-op 187
-> merged PR #187 as a0b65e8a60 (squash); primary /home/lumen/bin fast-forwarded to c4cb6d7
```

Current code, confirmed live in `/home/lumen/bin` (main, c4cb6d7):
- `maint-ops/litellm-upgrade:18-19` — `TARGET_DIGEST`/`FALLBACK_DIGEST` now point at v1.96.2/v1.88.6.
- `maint-ops/litellm-upgrade:54-62` — `verify()` greps the running container for
  `reject_url_valued_destination` whenever `$want` is `TARGET_DIGEST` or `FALLBACK_DIGEST` (item a;
  version string isn't reported by the API, so the running code is the only observable proof).
- `maint-ops/litellm-upgrade:120-140` — `apply()` still takes a `pg_dump` before upgrading
  (`PGDUMP=1` default), recorded in `$STATE`.
- `maint-ops/tests/harness_litellm.sh:3-13` — OLD/NEW are env-parameterized (no longer hardcoded to
  the old wq#2989 CRITICAL-patch pair); step B forces a verify failure and checks rollback to OLD;
  step E2 runs an independent `--verify` after the manual rollback (E), i.e. the old image's smoke
  suite is checked as its own harness assertion post-rollback, not only trusted via `rollback()`'s
  internal call.

### Item (d): scope note (DB-dump-restore vs. image-only rollback)

`rollback()` (`maint-ops/litellm-upgrade:110-118`) is **image-only** — it does not restore the DB
from the pre-upgrade dump. This was a deliberate choice by the a2/a3 attempt (see
`maint-ops/tests/harness_litellm.sh:7-13`), not an oversight: a restore-from-dump rollback is new,
untested, destructive database code (DROP/RENAME against a live postgres) that no build worker can
exercise (docker is guard.sh-restricted to `ps`/`logs` for this role), so it was not built blind.
The wq#2990 rehearsal already established the fallback that makes image-only rollback safe for
*this* upgrade: the old image boots cleanly on the migrated (+26) schema. The testable half of item
(d) — confirming the old image's smoke suite still passes after a rollback — is what step E2 checks.
If a future LiteLLM bump's migrations are not backward-compatible with the old binary, image-only
rollback stops being safe and would need this DB-restore path built (and exercised) properly, not
retrofitted here.

### Item (b): residual gap — harness has not been executed

The harness's actual "re-run to failure with the v1.96.2 digest, including the forced-verify-failure
rollback" (steps A-F, E2 in `maint-ops/tests/harness_litellm.sh`) requires running `docker compose`
against a scratch project + scratch DB. `docker` is denied to all build-worker sessions except
`ps`/`logs` (`guard.sh`, tier-0 instinct 4/deny-list) — confirmed blocking three separate attempts
on this ticket (`/home/lumen/foreman/_runs/wq3097/failure.json`: `docker denied except ps/logs`).
This is a role boundary, not a per-attempt bug: no build worker can execute this harness. It needs
an orchestrator-privileged session (ssh to Lumen, same session class the actual window op itself
runs under per the ticket's own "Orchestrator-only (ssh)" line) to run:

```
OLD=<current prod digest per research/wq1402_upgrade_batch_manifest.md's window-1/2 split> \
NEW=sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63 \
bash /home/lumen/bin/maint-ops/tests/harness_litellm.sh
```

before this op is added to the `wq#1402` window queue. Do not queue until that harness run is green
end-to-end (checks A-F, E2 in the script).

## Item (c): caller scan for the 3 PR 36011 breaking shapes

`scan_callers_pr36011.py` in this directory, re-run fresh against every directory this worker was
granted (`/home/lumen/worktrees/wq3097-a4`, `agents/work-queue`, `mailbot`, `pokemon`, `bin`, `kb`,
`projects/nest`):

```
== 1. path-valued deployment name: 0 hit(s)
== 2. litellm_metadata[bracket] form field: 0 hit(s)
== 3. /health/test_connection: 0 hit(s)
== config knobs already set (...): 0 hit(s)
TOTAL=0
```

Same residual blind spots as the a1 attempt noted: `/home/lumen/agent/litellm_config.yaml` (the
live server config) and n8n's own workflow store are outside this worker's directory grant. n8n is
already known-empty (`research/wq1402_upgrade_batch_manifest.md` F2, verified 2026-09-28). Page
Assist is an OpenAI-compatible client (model always in the JSON body) and structurally can't hit
shapes 1/2, and has no reason to call the admin-only `/health/test_connection` route — a design
inference, not a grep hit; flag for the orchestrator to eyeball `litellm_config.yaml`'s
`general_settings`/`litellm_settings` blocks once inside a session that can read `/home/lumen/agent`.

## Next step (not this ticket)

Once an orchestrator-privileged session runs the harness green, queue per the shape already worked
out in `research/wq1402_upgrade_batch_manifest.md` (window slot 2, 2026-10-12 02:00-05:00 PT — the
GHSA-4xpc-pv4p-pm3w Critical patch.3 line has first claim on window 1, 2026-10-05):

```
lumen-window queue add --id wq3097-litellm-3cv6 --window fleet --node lumen --est-sec 300 \
  --ticket 3097 --pri 1 \
  --precheck '/home/lumen/bin/maint-ops/litellm-upgrade --precheck' \
  --cmd '/home/lumen/bin/maint-ops/litellm-upgrade --apply' \
  --verify '/home/lumen/bin/maint-ops/litellm-upgrade --verify'
```

Fallback if v1.96.2 canary-pull or precheck fails: `TARGET_DIGEST=sha256:0c73da8d132b893bfa037511553bafbc9e2ff6abe9b474a66e0e9a354c0be064`
(v1.88.6, same fix) via env override.
