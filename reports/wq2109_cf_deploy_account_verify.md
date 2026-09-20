# wq#2109 — cf-deploy liveness: the token is alive; the verify endpoint was wrong

Worker: claude-opus-5, 2026-09-20. Judgment call on the wq#2108 CONTRADICT of PR#692 (4ed8a34).
Input evidence: `/home/lumen/agents/work-queue/reports/2108_cf_deploy_verify.md`.
Token value never printed; every probe reports status only.

## Decision

wq#2108 is upheld. `/home/lumen/mailbot/.cf_deploy_token` is an **account-owned** Cloudflare
token, alive, with Workers Scripts but no D1. wq#2105 verified it at `/user/tokens/verify`,
which only knows user-owned tokens and answers 401 "Invalid API Token" for an account one —
forever, on a perfectly healthy token. Everything wq#2105 built on that 401 (a permanent
URGENT page, an unsatisfiable binding, a physical re-mint ticket, a D1:Edit scope claim) is
wrong, and the fix is the verify URL, not the token.

## Evidence (reproduced independently, not taken from wq#2108)

`liveness.cred_alive(/home/lumen/mailbot/.cf_deploy_token, <url>)`:

| endpoint | verdict |
|---|---|
| `/user/tokens/verify` | STILL_BLOCKED, rejected (401) |
| `/accounts/3f413ce0…/tokens/verify` | SATISFIED, verify 200 |
| `/accounts/3f413ce0…/workers/scripts/lumen-mailbot` | SATISFIED, verify 200 |
| `/accounts/3f413ce0…/d1/database/06f40caf-…` | STILL_BLOCKED, rejected (401) |

Sentinel `check_bearer('cf-deploy', rec)` on the same token, the two verify URLs:
user → `level=dead, verify HTTP 401`; account → `level=ok, active, no expiry`. One field in
the ledger is the whole difference between a nightly false page and a correct one — the
sentinel reads `verify_url` out of the ledger (credential_expiry.py:100, :117-124), so no
sentinel code change was needed for the page.

Whole-probe check with the amended ledger: `probe()` → `ok=True critical=False,
"8 credentials ok"`. The false URGENT is gone.

## What changed (foreman)

- `harness/liveness_ledger.yaml` — cf-deploy: `verify_url`/`probe` → the account endpoint;
  `token_type: account`; scope text says Workers Scripts:Edit and no longer claims D1;
  `last_verified` records ALIVE, not DEAD. cf-dns-edit / cf-email-routing get
  `token_type: user` (evidenced by their 200 at the user endpoint, 2026-09-07).
- `scope_probes.d1_edit` — a real D1 probe against the lumen-mail database
  (`mailbot/wrangler.toml` `database_id`). A token verify proves the token lives and says
  nothing about scopes; the D1 claim now has to earn itself.
- bindings — 1269 re-pointed at the account endpoint (SATISFIED, verified); new binding 2106
  = the D1 probe (STILL_BLOCKED, verified), so the physical ticket self-closes when the
  scope lands.
- `harness/liveness.py` — a 401 from the CF **user** verify endpoint now names the account
  endpoint in its detail, so the next reader does not repeat the misdiagnosis.
- `harness/tests/test_liveness_ledger.py` — every Cloudflare credential must declare
  `token_type` and carry the verify URL that type answers on; the `probe:` string must use
  the record's own `verify_url`; cf-deploy's scope may not claim D1:Edit without a D1 probe.
  `harness/tests/test_liveness.py` — the 401 hint, and no token value in the detail.
  28 pass (`python3 -m pytest harness/tests/test_liveness_ledger.py harness/tests/test_liveness.py -q`).

## Tickets

- **wq#2106 re-scoped**, not retired: the D1 heal is still needed
  (LUMEN_PUBLIC_RUNBOOK.md:41,48-49 shells `wrangler d1 export` + `d1 migrations apply`),
  so the physical act is "add D1:Edit to the existing token", not "re-mint a dead one".
  Its Liveness probe is now the D1 endpoint — satisfiable, and self-closing.
- **wq#2111 filed** for the two edits this worker could not make: `/home/lumen/sentinel` is
  outside a foreman worktree's allowed dirs. The sentinel `REMINT` dict has no `cf-deploy`
  key (pages print the generic fallback) and its docstring still documents the Cloudflare
  extractor as the user endpoint.

## The general lesson

A credential has two independent facts — *does it live* and *what may it do* — and one
endpoint answers only the first. wq#2105 asked one question, got a 401 from an endpoint that
could not have answered it, and spent the answer on a re-mint request for a live token. The
ledger now separates the two: `verify_url` for liveness, `scope_probes` for authority, and a
test that refuses to let either claim outrun its probe.
