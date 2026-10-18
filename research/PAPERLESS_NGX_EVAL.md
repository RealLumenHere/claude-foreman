# wq#4162 — Paperless-ngx Eval: RE Project Docs + Business Receipts

ticket: wq#4162 | date: 2026-10-18

## VERDICT: **CONDITIONAL GO** — adopt for business receipts now; scope RE-doc ingestion to third-party PDFs only, not the existing `/home/lumen/re/` store

## 1. What it is

`paperless-ngx` (github.com/paperless-ngx/paperless-ngx, community fork of the original
`paperless`) is a self-hosted document archive: a consume-folder or IMAP mailbox feeds in
scans/PDFs, a bundled OCR step (`ocrmypdf`/Tesseract) makes them full-text searchable, and
a rule-based "Auto" matcher assigns tags/correspondents/document types/storage paths based
on content (docs.paperless-ngx.com/usage/, /advanced_usage/, fetched 2026-10-18 via
`fleet-search web`). Stack is Django web app + PostgreSQL + Redis + a worker process;
Tika/Gotenberg are optional add-on containers for Office-document conversion, not needed
for scanned PDFs/receipts.

## 2. Resource requirements vs what the fleet has free

Figures from the project's own docs and community reporting (selfhostpicks.com
hardware-requirements page, r/selfhosted "what specs" thread, both fetched 2026-10-18):

| state | RAM | CPU |
|---|---|---|
| idle | ~0.8 GB | negligible |
| recommended | 4 GB | — |
| during OCR | ~1.5-2 GB | spikes to 2+ cores for seconds-to-minutes per batch |

Current headroom on `lumen` (the only realistic host — `free -h`/`nproc` run this session):
60 GiB RAM, 53 GiB available, 4 cores, 248 GB free disk. The idle/recommended footprint is
trivially affordable. The actual constraint is **CPU contention during OCR bursts**: `lumen`
is also the gateway + dispatcher host running LiteLLM, wq, and ~35 other containers
(`foreman/fleet-config/docs/ARCHITECTURE.md:12`), and this box only has 4 cores total. A
multi-document OCR batch spiking 2 cores would meaningfully compete with LiteLLM/dispatcher
load. Mitigation is the same pattern already used for other IO/CPU-heavy fleet jobs — a
`Nice=`/cgroup CPU cap on the paperless worker container (c.f. `re-sync`'s `Nice=15` on its
checksum pass, `foreman/ops/re-sync/DESIGN.md:33`) — not a reason to skip, but a deploy
condition.

No other node is a better fit: `isis` is GPU-inference-only and paperless-ngx's OCR is
CPU-bound (no benefit); `sobek` and `maat` are policy-restricted to specific services
(postgres/cloudflared, and restart-locked respectively); `bes` is a backup copy-target, not
an app host. `lumen` is the only node provisioned as a general docker app host.

## 3. Value: business receipts — genuine unmet need

Searched `kb/`, `mailbot/` for any existing receipt/expense-tracking tooling: none found
(`grep -rli receipt` turns up only an unrelated chatbot roleplay line and verification-receipt
language in `ISSUE-LOG.md`, neither a document store). Business receipts currently have no
archive, no OCR, no search. This is the strongest case in the ticket: snap a photo or save a
PDF receipt, drop it in paperless-ngx's consume folder or forward it to its mail-fetch inbox,
get OCR'd text + auto-tagged correspondent/amount-bearing document, searchable later for
tax/expense reconciliation. Low effort, clear gap, no competing system to reconcile against.

## 4. Value: RE project documentation — overlaps an existing, working system

The ticket's other stated use is "every RE project generates docs." The canonical store for
RE project artifacts is already `/home/lumen/re/`, synced from edge workstations (Thor,
maksoor) by `re-sync` (`foreman/ops/re-sync/DESIGN.md:1-4`), and RE *findings* already live
as git-tracked markdown in `foreman/reports/`, `kb/ISSUE-LOG.md`, and `TOOL_REGISTRY.md` —
all plain-text, already full-text-greppable, already versioned, already the convention this
repo's own eval-doc history uses (section 0 of this doc is an instance of it). Pointing
paperless-ngx at *that* content would fragment a system that already works and is grep/git
native, for a search feature (OCR) those files don't need because they were never scanned
images to begin with.

Where paperless-ngx does add value on the RE side: third-party reference material that
*is* scan-shaped and currently has no home — vendor datasheets, chip manuals, teardown
photos with handwritten annotations. Those are genuinely unsearchable today (a PDF datasheet
dropped in a project dir isn't OCR'd or tagged) and are a legitimate consume-folder target.
Scope the ingestion there, not at the markdown/git corpus.

## 5. Install path

Official path is Docker Compose (`docker-compose.yml` from the project's `docker/compose`
tree, per docs.paperless-ngx.com/setup/) — web + postgres + redis services, `.env` for
`PAPERLESS_OCR_LANGUAGE`/consume-folder/mail-fetch config, a bind-mounted consume directory.
No GPU, no special kernel modules. Community posts (dev.to "Self-Host Paperless-ngx With
Local AI", r/Paperlessngx "fully automated document management") show a `paperless-gpt`
post-consume-script integration that routes OCR'd text through an LLM for smarter
title/tag suggestions — notable because the fleet already runs a LiteLLM gateway
(`:4000`, `ARCHITECTURE.md:28-31`) that such a script could point at directly instead of a
cloud API, if the "Auto" matcher's rule-based tagging proves too coarse in practice. Not
needed for a first cut; worth revisiting once there's a backlog of real documents to tune
against.

## 6. Recommendation

- **GO now**: stand up paperless-ngx on `lumen` via Docker Compose, consume folder +
  mail-fetch inbox scoped to (a) business receipts and (b) RE-project third-party PDFs
  (datasheets/manuals/teardown scans) — not the existing `/home/lumen/re/` or markdown
  report corpus, which stay on their current git/grep workflow.
- **Deploy condition**: cap the OCR worker's CPU share (cgroup/`Nice=`) so ingestion bursts
  don't starve LiteLLM/dispatcher on `lumen`'s 4 cores.
- **Physical-task, not a gate** (instinct 3): feeding paper receipts in requires an actual
  scan/photo action only Frank can perform — this doesn't block standing up the stack, it's
  a standing to-do once the consume folder exists.
- **Not now**: an LLM-assisted tagging layer (`paperless-gpt` against the house LiteLLM
  gateway) — revisit once there's enough real-document volume to know whether the built-in
  Auto matcher's rules are sufficient.
