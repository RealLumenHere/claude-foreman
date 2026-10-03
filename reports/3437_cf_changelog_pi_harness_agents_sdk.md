# wq#3437 triage: CF changelog "Run the Pi Durable harness on Cloudflare with the Agents SDK"

Source: https://developers.cloudflare.com/changelog/post/2026-10-02-pi-harness/ (2026-10-02), filed by cf-changelog-watch (wq#3335).
Read via `fleet-search read` on 2026-10-03.

## Verdict: informational, no action required. Close.

The post only adds something new. It does not change anything we run. Our only Worker is
`lumen-mailbot` (`/home/lumen/mailbot/wrangler.toml:1-5`: `main = "src/worker.js"`, `nodejs_compat`).
It declares no Durable Object bindings and does not depend on the `agents` package. A grep of
`package.json`/`wrangler.*` under mailbot, foreman, and agent for `"agents"`, `durable_objects`, and
`@earendil` found nothing. The keyword match ("Agents", "Workers") is about the product names. It
does not refer to anything we use.

## What changed (summary)

- New `PiHarness` class in the Cloudflare Agents SDK (beta). It is a "Lifecycle capability" that
  runs the Earendil Pi Durable agent harness inside a Durable Object, so a turn interrupted by a
  restart, crash, or network drop resumes from persisted state.
- Install: `agents@latest` plus `@earendil-works/pi-durable` and `@earendil-works/pi-ai`. Both Pi
  packages are optional peer dependencies of `agents`.
- Models are set through `agents/models/pi-ai`, which supports AI Gateway and Workers AI models or
  an existing pi-ai provider. Tools and system-prompt sections are added through Pi extensions.
- Cloudflare calls this its first step toward supporting third-party agent harnesses. More
  Lifecycle capabilities are promised later.

## Possibly useful later (nothing is filed from this ticket)

1. **Durable mailbot turns:** the mailbot does all of a reply in one Worker invocation. If replies
   ever grow into multi-step tool use that is lost on eviction, a Durable Object agent would fit
   (PiHarness or the plain Agents SDK). Nobody has reported that failure, so this stays a note.
2. **Fleet agents off-box:** our agent harness runs on Lumen through Claude Code and fworker. Moving
   it onto Workers would mean a rewrite, and the beta does not justify one. If a later
   cf-changelog post marks Lifecycle capabilities GA, that ticket is the place to look again.
