# wq#3436 triage: CF Access "strict service token authentication" setting

Source: https://developers.cloudflare.com/changelog/post/2026-10-02-strict-service-token-authentication/ (2026-10-02), filed by cf-changelog-watch (wq#3335).
Read via `fleet-search read` on 2026-10-03.

## Verdict: no breakage today. Close, with one follow-up filed.

## What changed

When the org-level setting is on, a request carrying `CF-Access-Client-Id`/`-Secret` headers:

- gets 401/403 on failure, never a 302 to the login page;
- can only be authorized by a **Service Auth** policy. Allow policies and any `CF_Authorization` cookie on the request are ignored;
- gets no `CF_Authorization` cookie back, so every request must resend the token headers;
- shows up in Access auth logs on failure, if the token is recognized.

Orgs created on or after 2026-10-05 have it forced on. Older orgs keep it off until it is toggled
(dashboard: Zero Trust > Access controls > Access settings > Manage service tokens; API: `strict_service_token_auth`).
Cloudflare recommends existing orgs turn it on.

## Our exposure

Our Zero Trust org predates 2026-10-05, so the setting is **off** and nothing changes until someone enables it.

Fleet use of service tokens:

- Every service-token-gated app reuses one policy, `Worker service token` `e23d092b-c2d2-4093-b1ec-ecd1a08ec2c0`:
  mcp-failover (`ops/wq2609-nephthys-mcp-failover-a1/apply-nephthys.sh:43`), the DC endpoints
  (`ops/wq2608-dc-endpoint-bes-a1/dns_access_setup.sh:24`, `ops/wq2607-dc-endpoint-anubis/README.md:95`),
  and Commander (`ops/p1-commander/install.sh:111`).
- Callers already send the token headers on every request (curl/MCP clients). None of them depend on
  the cookie being reused or on a 302. The probes expect 403 without a token
  (`ops/wq2609-nephthys-mcp-failover-a1/verify.sh:9`). Strict mode only applies to requests that carry
  token headers, so those probes don't change.
- `bin/access-jwt-guard.cjs:114` already tells service-token JWTs apart from user JWTs by `common_name`.
  The origin keeps getting `Cf-Access-Jwt-Assertion`, and strict mode changes nothing on that side.

**Unknown:** whether `e23d092b` uses the `Service Auth` (`non_identity`) action or `Allow` with a
service-token include. No file in the repo records its decision field, and Access policies can't be read
from a worker grant (`ops/wq2607-dc-endpoint-anubis/README.md:97-98`). If it is `Allow`, enabling strict
mode would 403 every service-token caller: Commander, mcp-failover, and the DC endpoints.

## Follow-up

wq#3438 is filed to read back the decision on `e23d092b`, switch it to Service Auth if needed, and then
turn strict mode on, as Cloudflare recommends. Strict mode closes the cookie-reuse path and logs failed
token attempts, which is worth having on the Commander path. Per instinct 5 this goes through the
Commander-path change discipline.
