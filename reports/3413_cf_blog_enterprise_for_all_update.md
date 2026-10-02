# wq#3413 triage: CF blog "Updates on our pledge to make Cloudflare features accessible to everyone"

Source: https://blog.cloudflare.com/enterprise-for-all-update/ (2026-10-02), filed by cf-changelog-watch (wq#3335).
Read via `fleet-search read` on 2026-10-02.

## Verdict: informational, no action required. Close.

Nothing in the post changes behavior we depend on or removes anything we use.
Our Cloudflare footprint is the mailbot Worker plus mail-probe, D1 `lumen-mail`, Email Routing,
the cloudflared tunnel, and Access (see `LUMEN_PUBLIC_RUNBOOK.md`). The post's keyword match
on "Access" refers to Access RBAC and the Access IdP config inside Organizations. It is not
a change to the Access apps or policies we run.

## What changed (summary)

- Logpush and Logpush Transformers (SQL filter/redact before delivery) are now on Free/Pro/Business with pay-as-you-go pricing. Custom Dashboards are on all plans.
- RBAC: Workers now get per-resource roles, joining R2 and Access.
- The New Account button lets anyone self-create extra free accounts. Each one is billed separately.
- Organizations (group accounts, shared admins/audit/WAF/Gateway/Access IdP): Enterprise GA in October 2026, free accounts in early 2027.
- The Terraform provider is generated from the API. Resource Tagging is available on all plans.
- Authentik IdP, SCIM audit logs, and SCIM 2.0 Group Sync are out. MCP Server Portals are GA.
- Billable-usage dashboard/API, plus budget alerts that are on by default. Hard spending caps get early availability in Q4 2026.
- Higher limits: Worker startup 1 s (was 400 ms), script size 64 MiB (was 10 MB), up to 1M subrequests per request, 32 MiB WebSocket messages. A Durable Object with an active outbound connection now stays alive up to 15 min. Headers go up to 128 KB. Browser Run allows 200 concurrent browsers. Workflows, Containers, Vectorize, and Pages limits are also raised.
- KV limits are not mentioned. The KV write cap from wq#991 still applies.

## Possibly useful later (nothing is filed from this ticket)

1. **Hard spending caps (Q4 2026):** once they ship, this is a cheap safeguard for the mailbot account. The cf-changelog watch should catch the GA post, and that ticket is the right place to act.
2. **Logpush on non-Enterprise plans:** Workers Trace Events could go to R2 as a lasting mailbot log. Today we only have `wrangler tail`, which is ephemeral. It costs money and nobody has asked for it, so it stays a note and not a ticket.
3. **Free-tier Organizations (early 2027):** not useful until we run more than one CF account.
