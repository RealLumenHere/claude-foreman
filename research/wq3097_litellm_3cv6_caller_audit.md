# wq#3097 caller audit: GHSA-3cv6-jpf6-8222 / upstream PR 36011 breaking shapes

Read-only research, 2026-09-29. Nothing was upgraded, restarted or queued by this document.
Companion to the code changes in `lumen/lumen-bin` PR #187 (litellm-upgrade + harness_litellm.sh),
item (c) of wq#3097's pre-queue TODO list.

## What was checked

Upstream PR 36011 (the fix for GHSA-3cv6-jpf6-8222 / CVE-2026-84377) tightens LiteLLM's proxy
input handling in three ways that can break an existing caller:

1. A URL-valued deployment name in the request path is now rejected instead of proxied.
2. A multipart `litellm_metadata[...]` field carrying a restricted/reserved param name is now
   rejected instead of merged into the request.
3. `/health/test_connection` calls that supply their own connection fields (rather than using a
   configured deployment) are now rejected.

## Method

Grepped every repo this worker can reach — `/home/lumen/foreman`, `/home/lumen/bin`,
`/home/lumen/agents/work-queue`, `/home/lumen/mailbot`, `/home/lumen/pokemon`,
`/home/lumen/projects/nest`, `/home/lumen/kb` — for:

- `litellm_metadata`, `test_connection`, `/health/test` (the literal shapes)
- `openai/deployments/{`, `/engines/{` (Azure-style URL-in-path deployment routing — the actual
  shape of breaking change 1, which a naive `litellm_metadata`/`test_connection` grep would miss)
- `provider_url_destination_allowed_hosts`, `allow_client_side_credentials`,
  `configurable_clientside_auth_params` (the three upstream config knobs that pre-emptively widen
  the new restrictions — a hit here would mean a caller needs one of the exemptions)

Zero hits anywhere, on every pattern.

## Findings

- **n8n**: the one workflow-engine caller present on the fleet. Per
  `research/wq1402_upgrade_batch_manifest.md:170`, n8n "was unused (no workflows, credentials,
  webhooks or executions, and no owner account)" as of the wq#1402 survey — it cannot be
  exercising any of the three shapes above because it makes no LiteLLM calls at all.
- **Fleet-internal callers**: `research/FLEET_SERVICE_ATLAS.md:76` lists litellm:4000's consumers
  (3 fleet researchers, minerva-classifier/proposal/eval/factory, lumen-model-watchdog,
  litellm-budget-alert, model-audit, dgm, skillhex, reflector, convo-learn, sentinel
  probe-synthetic-llm, langfuse-evaluator, LPP escalate paths, messenger `[m:]` picker). None of
  their source (searched across the repos above) references `litellm_metadata`,
  `test_connection`, or constructs a request path from a caller-supplied deployment name — every
  hit for the model identifier in these callers is a fixed string from the local model pool
  config, never a URL or user-controlled value threaded into the path.
- **Page Assist**: a browser extension (external client, not fleet-repo code) that talks to the
  LiteLLM-compatible `/v1/*` endpoints directly. Its request shapes aren't controlled by any repo
  in scope here, so there's nothing to grep for; it is a standard OpenAI-shaped chat client and
  has no reason to use any of the three shapes above (all three are proxy-admin / connection-test
  surfaces, not chat-completion surfaces).

## Conclusion

No internal caller exercises any of the three breaking shapes from PR 36011. This does not need a
code change on the caller side before the v1.96.2 upgrade proceeds. Remaining pre-queue items are
tracked in `lumen/lumen-bin` PR #187 (script changes) and the harness run + `lumen-window queue
add` step, both of which require an operator with docker/queue access this worker does not have.

## Note on item (d) (rollback)

The ticket's item (d) asked for "rollback = DB dump restore (26 migrations), not image-only; run
the smoke suite on the old image after rollback in the harness." PR #187 implements the second
half (an independent `--verify` call after the harness's manual-rollback step, so the old image's
smoke suite is its own checked assertion). It deliberately does not add DB-dump-restore rollback
automation: an earlier draft did (`rollback_db`/`--rollback-db`, doing `DROP`/`CREATE`/`ALTER
DATABASE ... RENAME` against the live postgres container), but that is genuinely destructive,
production-facing code this worker cannot execute even once — docker is guard.sh-restricted to
`ps`/`logs` here — and `litellm-upgrade`'s existing image-only rollback was already rehearsed OK
against a DB 26 migrations ahead (old image boots on the migrated schema, per the wq#2990
rehearsal). Shipping untested DROP/RENAME logic for an incident-response code path is worse than
not having it. If a future upgrade's schema drift isn't forward-compatible, that path should be
built and tested by an operator with real docker access, not authored blind.
