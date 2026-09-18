# wq#1970 — Vendor API Key Rotation, Operator-Free

Session: fworker build lane • 2026-09-18 • model claude-fable-5-1@high

## Why this ticket exists

wq#1747 (frank_gated, notes) pulled the Bes VictoriaLogs leak rows from the
`desktop-commander-mcp` supergateway body-log incident (wq#1698) and confirmed
two live vendor keys were exposed as Bearer headers: `OPENROUTER_API_KEY` and
`DEEPSEEK_API_KEY`, both sourced from `/home/lumen/agent/.env`. Its own
finding: *"rotation is dashboard-only for both vendors — the one part that
needs Frank."* Every future leak of this class repeats that same
Frank-in-the-loop bottleneck unless the fleet gets a self-service rotation
path per vendor. This doc is that survey plus a recommendation.

## Access note (read before trusting the vendor list below)

This build worker's sandbox does not include `/home/lumen/agent/` — every
direct read/grep against `/home/lumen/agent/.env` was refused by the path
guard ("may only search files in the allowed working directories"), which is
the correct behavior per instinct 4 (never print credentials) and the
worktree/`--add-dir` boundary rule. So the vendor list below is **not** a
direct dump of that file — it's reconstructed from indirect evidence across
files this session *could* read, cited per line below. Two direct
confirmations exist (`OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, from wq#1747's
own findings text); the rest are inferred from code/doc references to the
provider and should be confirmed by an operator with real access to
`/home/lumen/agent/.env` (var names only) before the rotation script is
wired up.

## Vendor inventory (var names only, no values)

| Var (evidence-based) | Vendor | Evidence |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter | wq#1747 findings note (`wq show 1747`): "OPENROUTER_API_KEY and DEEPSEEK_API_KEY from agent/.env leaked as Bearer headers — real third-party keys (OpenRouter usage 6.61...)" |
| `DEEPSEEK_API_KEY` | DeepSeek | same wq#1747 note; also `LUMEN_PUBLIC_RUNBOOK.md:97-98` ("top up or rotate `DEEPSEEK_API_KEY` in `/home/lumen/agent/.env`") and `ops/wq1252-lumen-public-guardian/checks_email.py:415-418` |
| `CEREBRAS_API_KEY` (name inferred) | Cerebras | `agents/work-queue/reports/1028_stale_sambanova_mapping.md:24`: `PROVIDER_TO_MODEL: {cerebras, mistral, openrouter, qwen, deepseek, gemini}` inside `agent/lumen_fleet_researcher.py`'s own provider map; `lumen-fleet-cerebras.service` runs that same script (`foreman/research/FLEET_SERVICE_ATLAS.md:230`) |
| `MISTRAL_API_KEY` (name inferred) | Mistral | same `PROVIDER_TO_MODEL` map + `lumen-fleet-mistral.service` |
| `GEMINI_API_KEY` (name inferred) | Google Gemini | same `PROVIDER_TO_MODEL` map lists `gemini` as a live provider (no retirement note, unlike sambanova) |
| `GITEA_TOKEN` | Gitea (self-hosted) | `agents/work-queue/reports/wq859_renovate_gitea.md:25`: "The shared fleet `GITEA_TOKEN` (`/home/lumen/agent/.env`)" |
| GitHub PAT var (name unconfirmed — `GH_TOKEN`/`GITHUB_TOKEN`) | GitHub | ISSUE-LOG #1: "Dead GitHub token... PAT rotated to `agent/.env` only; canonical `.secrets` left orphaned" |

**Not in `agent/.env`, out of scope for this rotation path:**
- **SambaNova** — fully retired fleet-wide in wq#381 (2026-07-19, 402
  paywall). `agents/work-queue/reports/1028_stale_sambanova_mapping.md:1-18`
  confirms `PROVIDERS["sambanova"]` and its `PROVIDER_TO_MODEL` entry were
  removed from `litellm_config.yaml`, no `lumen-fleet-sambanova.service`
  exists. Nothing to rotate; flagged here only so a future reader doesn't
  re-add it.
- **Cloudflare API token** — real and fleet-critical, but stored separately
  from `agent/.env`: `agents/work-queue/reports/vw_migration_306.md:20` lists
  `cf-workers-token` at `/home/lumen/.cf_workers_token`, and
  `LUMEN_PUBLIC_RUNBOOK.md:47,83` reads it from `.cf_deploy_token`. Covered
  below anyway since the ticket names it explicitly and the same rotation
  doctrine applies.

## Per-vendor: does a self-service rotation API exist?

*(Public API surfaces as of this model's Jan-2026 knowledge cutoff —
flagged `VERIFY` where the operator should re-check current vendor docs
before the one-time setup, same convention as
`ops/wq1590-deepseek-connect-guard/litellm_config_snippet.yaml:6-8`.)*

### OpenRouter — YES, has a provisioning API
OpenRouter's dashboard exposes a **Provisioning Key** (Settings →
Provisioning Keys) separate from regular API keys. That provisioning key
authenticates `POST/GET/DELETE /api/v1/keys` — create a new scoped/limited
key, list existing keys, revoke a specific key by hash. This is exactly the
mint-new/verify/revoke-old shape a rotation script needs, and matches the
ticket's own hint.
**One-time operator step:** create one Provisioning Key in the OpenRouter
dashboard, store at `/home/lumen/.secrets/vendor-rotation/openrouter-provisioning.key`
(0600). Never give this key to the LiteLLM proxy — only the orchestrator
rotation script reads it; the proxy gets the narrow operational key it
mints.

### DeepSeek — NO public key-management API (VERIFY)
platform.deepseek.com has no documented endpoint to create/list/revoke API
keys — key management is a dashboard-only flow, consistent with wq#1747's
"rotation is dashboard-only for both vendors" finding for this vendor.
**Mitigation (no automation possible):** (a) set a hard spend cap on the key
in the DeepSeek dashboard once, so a future leak is bounded in dollars, not
just detected after the fact; (b) route all fleet DeepSeek traffic through
the LiteLLM proxy only — no other service should hold this raw key — so the
blast radius of a future log leak is one process, not N; (c) file a
recurring (e.g. quarterly) wq ticket to hand-rotate as calendar hygiene
rather than only rotating reactively after a leak.

### Mistral — likely NO public key-management API (VERIFY)
La Plateforme (console.mistral.ai) supports creating multiple named API keys
in the console; no documented programmatic create/revoke endpoint is known
to this session. Treat as dashboard-only until an operator confirms
otherwise against current docs. Same mitigation shape as DeepSeek: spend
cap + proxy-only + calendared manual rotation.

### Cerebras — likely NO public key-management API (VERIFY)
Cerebras Cloud (cloud.cerebras.ai) console supports key creation; no
documented provisioning/management API found. Same mitigation shape as
DeepSeek/Mistral.

### SambaNova — moot
Already retired from the fleet (wq#381); no key exists to rotate. No action.

### Gemini (Google) — YES, if migrated to a GCP-managed key
A plain AI Studio key (aistudio.google.com) is dashboard-only, same as
DeepSeek. But Gemini API keys can also be created as regular **Google Cloud
API Keys** (`apikeys.googleapis.com`) scoped to the Generative Language API
inside a GCP project — and that service has a real management API
(`projects.locations.keys.create` / `.delete` / `.lookupKey`). This is
call-driven rotation, not dashboard-only, once the fleet's Gemini key lives
inside a GCP project rather than as a bare AI Studio key.
**One-time operator step:** create one GCP project (or use an existing one),
enable the API Keys API and Generative Language API, create a service
account with the API Keys admin role, and export that service account's
JSON credential once. Store at
`/home/lumen/.secrets/vendor-rotation/gemini-gcp-sa.json` (0600). The
orchestrator then mints/deletes scoped Gemini API keys under that project
via the service account — Frank is never needed again.

### Cloudflare — YES, has both create and in-place roll
Cloudflare's own API supports full self-service token lifecycle:
`POST /user/tokens` (create), `PUT /user/tokens/{id}/roll` (rotate the
secret value of an existing token **in place**, keeping its id/policy),
`DELETE /user/tokens/{id}` (revoke). The `roll` endpoint is the cleanest
primitive in this whole survey — no need to even re-provision policy on
rotation.
**One-time operator step:** create one API Token scoped narrowly to
"Account API Tokens: Edit" (or the account/zone-scoped equivalent — do not
grant broader scope than token management itself), store at
`/home/lumen/.secrets/vendor-rotation/cloudflare-root.token` (0600). The
orchestrator calls `roll` on the fleet's real Workers/Pages tokens on a
schedule; the root token itself never touches the proxy or any service env.

### Gitea (self-hosted) — CONDITIONAL, verify server version
Gitea's REST API supports `POST /api/v1/users/{username}/tokens` (create)
and `DELETE /api/v1/users/{username}/tokens/{token}` (revoke). Older Gitea
versions require **Basic Auth with the account password** to create a
token (a token cannot mint another token); newer Gitea (1.20+) added scoped
tokens where a token holding the `write:user` scope can call the token-
management endpoints itself.
**One-time operator step:** check the fleet's self-hosted Gitea version
(`gitea --version` or the `/api/v1/version` route). If ≥1.20 and scoped
tokens are enabled, mint one `write:user`-scoped admin token once, store at
`/home/lumen/.secrets/vendor-rotation/gitea-root.token` (0600) — fully
API-driven from then on. If the running version is older, the only
API-reachable path requires the account password itself, which is a
materially bigger secret to hand an automated script; recommend the
version-upgrade path over storing the password, and treat this vendor as
dashboard/manual until the upgrade lands.

### GitHub — fine-grained PATs: NO creation API by design; migrate instead
GitHub deliberately does not expose an API to create or rotate a PAT
(classic or fine-grained) — this is a security choice, not a gap, and no
operator workaround changes it. The fleet's actual self-rotating
alternative is a **GitHub App**: its installation access tokens
(`POST /app/installations/{id}/access_tokens`, authenticated by a JWT signed
with the App's private key) are fully API-mintable, short-lived (1h,
auto-expiring — smaller blast radius than a PAT by default), and scoped to
exactly the repos/permissions the App was installed with.
**One-time operator step:** create one GitHub App (org or user level as
appropriate), generate its private key once, install it on the repos the
fleet needs. Store the private key at
`/home/lumen/.secrets/vendor-rotation/github-app.pem` (0600). The
orchestrator signs a JWT and mints a fresh installation token per rotation
cycle (or even per use, since they're cheap to mint) — replaces every
GH_TOKEN/PAT the fleet currently hand-rotates.

## Storage doctrine (matches existing fleet convention)

Per `foreman/research/INFISICAL_EVAL.md:14-19` (citing
`agents/work-queue/reports/vw_migration_306.md:6-7`): *".env files = RUNTIME
source. VW = RECOVERY source of truth. Sync: human rotates in VW -> updates
.env. Never the reverse."* This ticket extends that doctrine one level up:

- **Root/provisioning credentials** (OpenRouter provisioning key, Cloudflare
  root token, Gitea admin-scoped token, GitHub App private key, Gemini GCP
  service-account JSON) live **only** at
  `/home/lumen/.secrets/vendor-rotation/<vendor>.{key,token,pem,json}`,
  mode 0600, owned by the account the orchestrator rotation script runs as.
- They are **never** placed in `agent/.env`, never in the LiteLLM proxy's
  environment, and never handed to any consumer service. Only the narrow,
  scoped, rotatable *operational* key that each root credential mints goes
  into `agent/.env` / the LiteLLM config — the exact split wq#1747 shows the
  fleet is missing today (the leaked keys *were* the root-equivalent
  credential, because DeepSeek/OpenRouter had no minted-and-scoped
  operational tier to leak instead).
- Vaultwarden stays the recovery copy for the root credentials too, same as
  every other secret in the `vw_migration_306.md` manifest.

## Rotation runbook shape (one orchestrator script)

```
for vendor in fleet_vendors:
    if vendor.has_rotation_api:
        root_cred = read_0600(f"/home/lumen/.secrets/vendor-rotation/{vendor}.key")  # never logged
        new_key = vendor.api.create_key(scope=vendor.min_scope, spend_cap=vendor.cap)
        stage_into(agent_env_or_litellm_config, vendor.var_name, new_key)
        reload_consumers(vendor)                      # systemctl reload/restart as needed
        ok = vendor.api.health_check(new_key)          # 1 cheap real call
        if not ok:
            rollback(); alert("rotation failed", vendor); continue
        vendor.api.revoke_key(old_key_id)
        audit_log.append(ts, vendor, new_key_id)        # id/hash only, never the value
    else:
        # DeepSeek, Mistral, Cerebras, Gitea-on-old-version (until upgraded)
        if rotation_due(vendor):
            wq_file_ticket(f"[secrets] manual rotation due: {vendor}", owner="frank")
        verify_spend_cap_still_set(vendor)               # cheap drift check, same pattern as
                                                          # github-token-check / lumen-secret-check
```

This mirrors the fleet's existing "cheap targeted detector" doctrine
(`foreman/research/INFISICAL_EVAL.md:30-35`, citing
`kb/tooling-findings-review.md:1-8`) rather than introducing a new secrets
platform — no Infisical, no second vault; this is a script + the root
credentials above, wired into cron/systemd the same way
`github-token-check`/`lumen-secret-check` already run.

## RECOMMENDATION

1. **Ship the orchestrator script for the 3 vendors with real rotation
   APIs first** (OpenRouter, Cloudflare, GitHub-via-App) — these fully
   close the Frank-in-the-loop gap wq#1747 hit, with the least new
   infrastructure. Gemini is a stretch 4th if the fleet migrates the key
   into a GCP project.
2. **DeepSeek, Mistral, Cerebras stay manual** until/unless their vendor
   consoles ship a management API — no operator step unlocks something
   that doesn't exist. Mitigate instead: spend caps (bounds a future leak
   in dollars, not discovery time), proxy-only key placement (bounds blast
   radius to one process), and a calendared rotation reminder ticket so
   "manual" doesn't silently mean "never."
3. **Gitea: check the running version before deciding.** If it already
   supports scoped self-tokens, it belongs in the automated group at zero
   extra cost; if not, upgrading it is cheaper and safer than storing the
   account password in an automated script.
4. **The one-time Frank actions, once, ever:** create the OpenRouter
   Provisioning Key, the Cloudflare token-management token, the GitHub App
   + private key, and (if pursued) the GCP project + service account for
   Gemini — five dashboard visits total, each done exactly once, each
   producing a credential that lives at
   `/home/lumen/.secrets/vendor-rotation/*` (0600) and is never handed to
   the proxy. Every future rotation of the operational keys those root
   credentials mint is fully autonomous from then on.

## What this session could not verify, and why

The exact current API surface for DeepSeek/Mistral/Cerebras key management
could not be confirmed live — this session has no network egress path to
vendor consoles from the sandbox, and the knowledge cutoff (Jan 2026) is 8
months behind today (2026-09-18); a vendor may have shipped a management API
since. Before wiring automation for any "NO"-verdict vendor above, an
operator with live access should re-check that vendor's current API docs —
flagged `VERIFY` inline per vendor rather than asserted as fact.
