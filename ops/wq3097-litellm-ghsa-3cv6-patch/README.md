# wq#3097 — LiteLLM GHSA-3cv6-jpf6-8222 (CVE-2026-84377, Moderate) patch prep

Gap G2 from `research/wq1402_upgrade_batch_manifest.md`: GHSA-3cv6-jpf6-8222 (authenticated SSRF +
provider-credential exfiltration) is not fixed anywhere on the 1.83 line Lumen runs. Fixed versions:
v1.88.6 (`sha256:0c73da8d132b893bfa037511553bafbc9e2ff6abe9b474a66e0e9a354c0be064`, fallback) and
v1.96.2 (`sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63`, target). The
upstream fix is https://github.com/BerriAI/litellm/pull/36011 (`reject_url_valued_destination`,
defined in `litellm/proxy/litellm_pre_call_utils.py`, called from
`litellm/proxy/image_endpoints/endpoints.py` and `litellm/proxy/common_request_processing.py` —
confirmed via GitHub code search against BerriAI/litellm, not guessed).

The wq#2990 rehearsal (prod-config + prod-DB clone) reported: healthy, +26 migrations, smoke 5/5,
zero config edits, old image boots on the migrated DB. That assessment doc
(`scratch/research/wq2990_litellm_3cv6_assessment.md`) was untracked scratch and is gone from disk
(a worktree cleanup took it with it — logged as issue #1026 below); this directory reconstructs the
parts needed to queue safely from the ticket text, `research/wq1402_upgrade_batch_manifest.md`, and
the live `litellm-upgrade` / `harness_litellm.sh` scripts.

This ticket's job (per the "TODO before queueing" list) is the four prep items, not running the
prod upgrade itself — that stays orchestrator-only, ssh, inside a maintenance window, because a
litellm restart is a fleet-wide LLM blip.

## Why the fix lives here, not in `/home/lumen/bin`

`/home/lumen/bin` is a separate repo from this one (`foreman`), and it is a guard-protected
`PRIMARY` tree: `guard.sh` refuses any worker `Edit`/`Write` there directly (wq#1719/wq#900). The
documented workaround (`kb/ISSUE-LOG.md` row 1025, wq#3086) is `lumen-pr start <repo> <branch>
--worktree`, which parks a *linked* worktree outside the primary tree. That command still works —
it created `/home/lumen/scratch/worktrees/bin-fix-wq3097-litellm-3cv6-window-op` on branch
`fix/wq3097-litellm-3cv6-window-op` off `origin/main`, confirmed the primary tree stayed on `main`
— but the resulting path isn't inside *this* worker's directory grant (`agents.yaml` /
`fleet-exec-policy.yaml` fixed my add_dirs at dispatch to `/home/lumen/bin` read-only, not
anything created under it later), so I can't `Edit`/`Write` inside it either. Logged as issue #1027
below. The branch exists and is empty; a bin-repo-scoped worker (or wq#3086's own lane) can reuse
it to land `patches/*.patch` below with `git apply` + `lumen-pr commit`.

## Item (a) + (d): `litellm-upgrade` — parameterize the fix marker, migration-aware rollback

`patches/litellm-upgrade.patch` (unified diff against `/home/lumen/bin/maint-ops/litellm-upgrade`
as of 2026-09-28 10:30 UTC):

- **(a)** `verify()`'s fix-marker grep was hardcoded to the patch.3 host-header marker
  (`request.scope` in `auth_utils.py`). Replaced with `FIX_MARKER_FILE` / `FIX_MARKER_PATTERN` env
  vars (default = the old values, so the already-queued patch.3 op is unaffected). Run the 1.96.2
  stage with `FIX_MARKER_FILE=/app/litellm/proxy/litellm_pre_call_utils.py
  FIX_MARKER_PATTERN=reject_url_valued_destination`.
- **(d)** `rollback()` only ever flipped the compose digest back and restarted — fine for the
  patch.3 same-line bump, unsafe for the 1.96.2 jump because it runs +26 migrations and an old
  binary against a migrated-forward schema is not a tested state. Added `MIGRATES=1`: `apply()`
  now always records the pre-upgrade dump path (`DUMP=`) in `$STATE`, and `rollback()`, when
  `MIGRATES=1`, stops the service, drops+recreates the DB from that dump, confirms
  `_prisma_migrations` is queryable again, *then* flips the compose file and restarts — matching
  the ticket's "rollback = DB dump restore, not image-only."

## Item (b): `harness_litellm.sh` — parameterize digests, add migration + post-rollback assertions

`patches/harness_litellm.sh.patch` (unified diff against
`/home/lumen/bin/maint-ops/tests/harness_litellm.sh`):

- `OLD`/`NEW`/`FIX_MARKER_FILE`/`FIX_MARKER_PATTERN`/`MIGRATES` are now env-overridable (defaults
  preserve the existing patch.3 run). Re-run to failure against the 1.96.2 digest with:
  ```
  OLD=sha256:e29d25e1952337eb7c9b605213865ae935c60f1689ad465cb5fc940b598174c9 \
  NEW=sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63 \
  FIX_MARKER_FILE=/app/litellm/proxy/litellm_pre_call_utils.py \
  FIX_MARKER_PATTERN=reject_url_valued_destination MIGRATES=1 \
  bash tests/harness_litellm.sh
  ```
  (`OLD` here is the patch.3 digest, i.e. the version prod will actually be on by the time this
  ticket's window runs, per `research/wq1402_upgrade_batch_manifest.md`'s window-1/window-2 split.)
- Added migration-count assertions around step C (apply) and step E (rollback): with `MIGRATES=1`
  the count must advance on apply and land back at the pre-upgrade count after rollback; with
  `MIGRATES=0` it must not move at all.
- Added step **E2**: an independent `litellm-upgrade --verify` call after the manual rollback (E),
  so the post-rollback smoke suite on the OLD image is its own checked harness assertion instead of
  only the internal `verify()` call that `rollback()` already makes on its own path — this is
  exactly item (d)'s "run the smoke suite on the old image after rollback in the harness."
- Fixed the final "prod compose still pins old digest lines" grep, which was hardcoded to the
  patch.3 digest fragment (`6c82d338`) — it now greps for `$OLD`, since by the time this harness
  runs for the 1.96.2 stage, prod's untouched baseline is the patch.3 digest, not `6c82d338`.

Neither patch has been applied or executed: `docker` is blocked to `ps`/`logs` only for workers
(guard.sh), and `/home/lumen/bin` is read-only as above. Applying and running to failure (including
the forced-verify-failure rollback) is unexecuted — flagged as a residual gap, not claimed done.

## Item (c): caller scan for the 3 PR 36011 breaking shapes

`scan_callers_pr36011.py`, run against every directory this worker was granted
(`/home/lumen/worktrees/wq3097-a1`, `agents/work-queue`, `mailbot`, `pokemon`, `bin`, `kb`,
`projects/nest`):

```
== 1. path-valued deployment name: 0 hit(s)
== 2. litellm_metadata[bracket] form field: 0 hit(s)
== 3. /health/test_connection: 0 hit(s)
== config knobs already set (...): 0 hit(s)
TOTAL=0
```

Read against the exact PR 36011 breaking-change text (fetched from GitHub, not guessed):

1. *"a deployment name supplied in the request path that parses as an http/https destination is
   now refused"* — no caller in the scanned dirs builds a request path with the model/deployment
   name in it (Azure-style `/openai/deployments/{model}/...`); every caller I could find uses the
   standard OpenAI-style body-based `model` field. **Not affected**, as far as this worker can see.
2. *"a multipart field such as `litellm_metadata[api_base]` is now subject to the same
   request-body parameter rules"* — zero occurrences of bracket-notation `litellm_metadata[`.
   **Not affected**, as far as this worker can see.
3. *"/health/test_connection no longer lets a request replace the routing or credential parameters
   of a configured model it names"* — zero callers of that endpoint. **Not affected**, as far as
   this worker can see.

**Residual gap, not closed:** `/home/lumen/agent/litellm_config.yaml` (the actual server config —
would show whether any deployment already sets `configurable_clientside_auth_params`) and n8n's
own workflow store are both outside this worker's directory grant. n8n is already known-empty (zero
workflows/credentials/webhooks, `research/wq1402_upgrade_batch_manifest.md` F2, verified
2026-09-28), so that part of item (c) is closed. Page Assist wasn't found as a checked-in config
anywhere reachable; by protocol design (OpenAI-compatible client, model always in the JSON body) it
structurally can't hit shape 1 or 2, and it has no reason to call the admin-only
`/health/test_connection` route, but this is a design inference, not a grep hit — flag for the
orchestrator to eyeball `litellm_config.yaml`'s `general_settings`/`litellm_settings` blocks once
inside a session that can read `/home/lumen/agent`.

## Status vs. the ticket's queue gate

| item | status |
|---|---|
| (a) fix-marker parameterization | patch written, not applied/run (bin repo read-only to this worker) |
| (b) harness parameterization + re-run to failure | patch written; re-run to failure **not executed** (docker blocked to ps/logs for workers) |
| (c) caller grep | done — 0/3 shapes hit across every dir this worker can reach; 2 residual blind spots noted above |
| (d) migration-aware rollback + post-rollback smoke | patch written, not applied/run |

**Do not queue yet.** The patches need a bin-repo-scoped worker (or Frank) to `git apply` them in a
linked worktree (branch `fix/wq3097-litellm-3cv6-window-op` already exists, see above), then
actually run `harness_litellm.sh` to failure with the v1.96.2 digest — docker execution is outside
this worker's grant, so that step is unverified. Once green, the queue command (parallel to the
patch.3 line in `research/wq1402_upgrade_batch_manifest.md`) is:

```
lumen-window queue add --id wq3097-litellm-3cv6 --window fleet --node lumen --est-sec 300 \
  --ticket 3097 --pri 1 \
  --precheck '/home/lumen/bin/maint-ops/litellm-upgrade --precheck' \
  --cmd 'TARGET_DIGEST=sha256:154e23bb5f31b1f10e16392a8ef299bd2cde08de3a64a6849002cfcc25ce3c63 \
         FIX_MARKER_FILE=/app/litellm/proxy/litellm_pre_call_utils.py \
         FIX_MARKER_PATTERN=reject_url_valued_destination MIGRATES=1 \
         /home/lumen/bin/maint-ops/litellm-upgrade --apply' \
  --verify 'FIX_MARKER_FILE=/app/litellm/proxy/litellm_pre_call_utils.py \
            FIX_MARKER_PATTERN=reject_url_valued_destination \
            /home/lumen/bin/maint-ops/litellm-upgrade --verify'
```

Fallback if v1.96.2 canary-pull or precheck fails: same shape with `TARGET_DIGEST=sha256:0c73da8d132b893bfa037511553bafbc9e2ff6abe9b474a66e0e9a354c0be064`
(v1.88.6) and no `MIGRATES=1` unless that line is separately confirmed to also run migrations.
Next available fleet windows per `research/wq1402_upgrade_batch_manifest.md`: Mon 2026-10-05,
Mon 2026-10-12 (both 02:00-05:00 PT) — this op isn't assigned to either window slot yet; the
patch.3 line (GHSA-4xpc-pv4p-pm3w, Critical) has first claim on window 1, so window 2
(2026-10-12) is the more likely slot for this one.
