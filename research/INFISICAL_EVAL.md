# wq#1303 — Infisical Eval: Fleet Secrets vs .secrets/ (and vs Vaultwarden)

ticket: wq#1303 | date: 2026-09-03

## VERDICT: **SKIP**

## 1. The premise needs correcting first

The ticket frames this as Infisical vs flat `.secrets/` files. That framing is stale: the fleet
already replaced "flat files are the only copy" with a real secret-of-record. Vaultwarden runs on
Maat, backed by its own Postgres, watched by a dedicated probe (`R2_watchdog_consolidation.md` W11:
`/alive` state-change -> ntfy), and is flagged the "crown jewel — vault primary" in the maintenance
audit (`opsaudit_a11_maintenance_window.md:104,292`). `/home/lumen/bin/vw-migrate-secrets` (v2, `bw
import`) is the one-shot loader that pushes `.secrets/*` into it. Doctrine, stated in the migration
report itself:

> `.env` files = RUNTIME source. VW = RECOVERY source of truth. Sync: human rotates in VW -> updates
> `.env`. Never the reverse.
(`/home/lumen/agents/work-queue/reports/vw_migration_306.md:6-7`)

So the real question isn't "flat files vs a vault" — it's "does Infisical fix something Vaultwarden
+ the existing detection crons don't."

## 2. What actually broke (ISSUE-LOG #1) and what fixed it

> Dead GitHub token (`github-frank.token` stale classic, 401) — PAT rotated to `agent/.env` only;
> canonical `.secrets` left orphaned — resynced; daily `github-token-check` cron opens aq on 401 —
> SOLVED (`/home/lumen/kb/ISSUE-LOG.md:7`)

Root cause was never "no central vault" — it was a human rotating a credential in one place and
forgetting to propagate it, with nothing watching for the drift. The fix that shipped was a cron
diffing live vs expected state and paging on failure, the same "Day-0 detector" pattern used
fleet-wide per ISSUE-LOG #10 (`/home/lumen/kb/ISSUE-LOG.md:16`) and endorsed by the tooling review's
core thesis: cheap targeted detectors beat installing a heavier platform
(`/home/lumen/kb/tooling-findings-review.md:1-8`).

## 3. What Infisical (Infisical/infisical, ~29k stars, Apache-2.0 core) would add

- **Central secret store with versioning + audit log** — redundant; Vaultwarden already fills this
  role and is already the audited/monitored one.
- **Machine identities / scoped service tokens** — real capability gap today (fleet tokens like
  `foreman-litellm.token`, `memoryd-service.token` are static files, not scoped/revocable per
  consumer). Genuinely nice-to-have, not a fix for the orphaned-secret failure mode.
- **Dynamic secret rotation** — Infisical's actual differentiator, but it only auto-rotates
  credentials where Infisical owns the rotation logic (built-in connectors: Postgres/MySQL/Redis
  passwords, a handful of cloud IAM providers). Every secret in the fleet's manifest that actually
  broke — GitHub PATs, Gitea tokens, Slack, ntfy, LemonSqueezy, eBay
  (`/home/lumen/agents/work-queue/reports/vw_migration_306.md:9-40`) — is a third-party API
  credential Infisical cannot rotate for you. You'd still hand-rotate on the provider's site and
  paste the new value in, exactly as today, just into a second UI.
- **Push-to-consumer sync (`infisical run --`, agent sidecar)** — the one feature that could close
  the actual gap (services fetch live instead of a copy going stale). But it requires rewriting
  every systemd unit / cron / script that currently sources `.env` or a `.secrets/*` file to invoke
  through the Infisical CLI/agent instead — a fleet-wide refactor of the runtime path, not a
  side-by-side install.

## 4. Self-hosted footprint

Infisical's self-hosted core is Postgres + Redis + the Node/Go backend (+ optionally a separate KMS
service for the newer versions) — 3-4 containers, roughly on par with what the tooling review
already flagged as too heavy when it challenged "Langfuse first" for a GPU-constrained solo box
(`/home/lumen/kb/tooling-findings-review.md`, footprint section). Adding it stands up a *second*
Postgres-backed secret platform next to Vaultwarden's Postgres-backed one on the same small fleet.

## 5. Recommendation: SKIP

1. Adopting Infisical alongside Vaultwarden creates two "sources of truth" for secrets — which is
   the exact shape of bug that caused ISSUE-LOG #1 (two copies, no propagation guarantee). It would
   add a second canonical location to go stale, not remove the one that already did.
2. Its headline fix (dynamic rotation) doesn't cover the fleet's actual secret population — external
   provider PATs/API keys — so it wouldn't have prevented ISSUE-LOG #1 or #40 either.
3. The failure mode is already patched at the cost of one cron (`github-token-check`,
   `lumen-secret-check`), consistent with the fleet's stated preference for the cheapest fix that
   closes the specific gap over installing a new platform.

**If a real gap gets worth closing later**, it's "no scoped/revocable machine tokens" and "no
drift check across the *whole* secrets manifest" (today only GitHub PATs get a dedicated check) —
both addressable by extending the existing `lumen-secret-check` pattern to the full
`vw_migration_306.md` manifest, not by standing up Infisical.

## 6. Addendum: the ISSUE-LOG #20 citation doesn't hold

The ticket cites ISSUE-LOG #1 *and* #20 as the token-rotation/orphaned-secret class. #1 checks out
(section 2 above). #20 does not: it's `lumen-pr open` silently swallowing a missing §5 PR body
because output was grep-filtered to the happy path, fixed as a habit (always read full output on
gating steps) — a PR-tooling bug, unrelated to secret storage or rotation
(`/home/lumen/kb/ISSUE-LOG.md:26`). Verified with `research/infisical_eval_check.py`'s
`issue_log_20_is_secrets_related` check below (expected FAIL, confirming the mismatch so a future
reader doesn't chase a bad citation). This doesn't change the verdict — #1 alone is the failure
class in question, and Infisical still wouldn't have fixed it — but the ticket's own citation should
be corrected for anyone revisiting this.
