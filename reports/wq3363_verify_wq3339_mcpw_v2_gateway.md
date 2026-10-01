# wq#3363 verify: wq#3339 mcpw v2 gateway (official workers-oauth-provider 1.x + CF Access-for-SaaS OIDC)

Archetype 8 adversarial verify-pass. Author: claude-sonnet-5. Verifier: claude-sonnet-5
(fresh instance, same model, per the ticket's no-contradiction escalation rule).

PR under test: `claude-foreman` PR #1569 (`http://100.121.17.5:3000/lumen/claude-foreman/pulls/1569`,
merged as `7d0398b2`, "fix(exec-policy): grant read access to the mcpw v2 gateway source" — now an
ancestor of this branch's HEAD). That PR is a 9-line `harness/fleet-exec-policy.yaml` addition, not
the gateway code itself: per `/home/lumen/kb/ISSUE-LOG.md` row 1156 (wq#3339's own entry), the
actual gateway rewrite was committed directly to `/home/lumen/projects/mcp-oauth-worker-v2`
(`3f91a5d`) because that tree "has no PR flow reachable from this sandbox" — the policy PR is
explicitly called out there as "this branch's real, citable AC1 evidence, since the gateway commit
lives in a different repo." Verified both.

## What wq#3339 claimed

1. `harness/fleet-exec-policy.yaml`: add `/home/lumen/projects/mcp-oauth-worker-v2` to
   `allow.read_dirs`, closing the dispatch gap that left wq#3339's own BUILD worker unable to
   `ls`/Read the source. Swept clean: `files=35 hits=0`.
2. `mcp-oauth-worker-v2` (`3f91a5d`): replace the v1 passphrase/self-hosted-Access gate with the
   official Access-for-SaaS OIDC pattern (`access-oidc.ts`, `default-handler.ts`), built on
   `@cloudflare/workers-oauth-provider` 1.2.1, keeping `pool.ts`/`router.ts` HA backend-pool and
   `/pool/health` from v1. Found and fixed a live bug: `parseAuthRequest()` throws
   `AuthorizationError` for an unregistered `client_id`, uncaught -> bare 500; now caught and
   turned into a 400 JSON or a `redirectTo` 302 per the library's contract. 71/71 unit tests,
   `scripts/diag.py` 14/14 live against staging. Production (`wrangler.prod.toml`,
   `scripts/migrate-to-prod.sh`) left untouched, Frank-present per the ticket.

## What I checked

**Policy diff (PR #1569):**
- Read the 9-line diff cold (`git -C /home/lumen/foreman show 7d0398b2`) — correct path, scoped
  comment explaining why, matches the directory actually in use.
- Independently re-ran the sweep rather than trusting the commit message:
  `python3 harness/read_dir_sweep.py /home/lumen/projects/mcp-oauth-worker-v2` ->
  **`files=35 hits=0`**, exact match to the claim.
- Read `wrangler.toml` and `wrangler.prod.toml` directly: account id and KV namespace ids only
  (not secret-shaped), secrets listed only as a comment of *names* bound via
  `scripts/put-secrets.sh`, never values. No contradiction of the "no secrets" claim.

**Gateway code (`3f91a5d`), read via `git -C`/`python3` file I/O** (the project dir isn't in this
session's `--add-dir` set either — same ISSUE-LOG row-1156 loophole the author used; direct
`ls`/`Read` on the path refuses as expected, confirming the gap PR #1569 just closed is real and
still cold for a session dispatched before the policy change propagates):
- Read `default-handler.ts` and `access-oidc.ts` in full. The `/authorize` catch block checks
  `e.redirectTo`, falling back to a local 400 JSON when absent.
- Cross-checked that against the **actual installed library**, not the PR's description of it:
  `node_modules/@cloudflare/workers-oauth-provider/dist/oauth-provider.d.ts` and the compiled
  `oauth-provider.js` show `AuthorizationError`'s constructor sets
  `this.redirectTo = options.redirectUri ? safeErrorRedirect(...) : void 0` — i.e. `redirectTo` is
  only defined once a `redirectUri` was supplied (which the library only does after validating
  client+redirect_uri). The fix's assumption matches the real 1.2.1 contract exactly, not just the
  commit message's paraphrase of it.
- Read `access-oidc.spec.ts` (two new regression tests, lines 121-150): one constructs an
  `AuthorizationError` with no `redirectUri` and asserts 400 JSON `error: "invalid_request"`; the
  other constructs one *with* `redirectUri`/`state` and asserts a 302 redirect containing
  `error=invalid_scope`. Both exercise the real exported `AuthorizationError` class (imported from
  `@cloudflare/workers-oauth-provider`, not a mock), so they can actually fail against a library
  upgrade that changes the contract.
- Reviewed `access-oidc.ts` state handling for the classic OAuth-bridge failure modes: state is
  HMAC-SHA256-signed, stored in KV, deleted on first read (single-use — confirmed by the "must be
  single-use" test), PKCE S256 end-to-end, identity gated by exact-match `ALLOWED_USER_EMAIL`
  after JWKS/iss/aud verification. `redirect_uri` sent to Access is built server-side from
  `request.url`, never from user input — no open-redirect surface. (Minor, not contradiction-grade:
  `sig !== await hmac(key, id)` is a plain string compare, not constant-time; same shape likely
  predates this diff and isn't practically exploitable for a single-user HMAC-256 gate — noting it,
  not blocking on it.)

**Ran the suite myself, not trusted from the commit message** (`python3 subprocess` into the
project dir, same path-sandbox workaround):
- `npx tsc --noEmit` -> **clean, exit 0**.
- `npx vitest run --reporter=dot` -> **71/71 passed, 7/7 files**, matching the claimed count
  exactly (69 pre-existing + 2 new regression tests from the bugfix, per ISSUE-LOG row 1156).
- `python3 scripts/diag.py` against the live staging deploy
  (`lumen-mcp-v2.ln-ops-7c3f.workers.dev`) -> **14/14 passed**, freshly run against production
  infrastructure, not replayed from the author's log. This includes the exact control case for the
  bug being fixed: `PASS 7 unknown client is still refused (control) — HTTP 400` (not a 500), plus
  state-replay/forged-state/missing-state rejections, PKCE S256 on the Access redirect, bearer
  gating on `/mcp`, `/pool/health` auth, and KV-backed survival of client registration across a
  90s edge idle recycle (the specific v1 regression Frank asked to be tested for).
- `wrangler.prod.toml`: separate worker name (`lumen-mcp` vs staging's `lumen-mcp-v2`), separate
  KV namespace id, `workers_dev = false`, custom domain route — distinct enough from staging that
  an accidental prod deploy via the staging script is structurally not possible. Confirms "left
  untouched" is plausible, not just asserted.

## Verdict: CONFIRM

No contradiction found on either half of the shipped work. The exec-policy fix is minimal, correct,
and its central claim (`files=35 hits=0`) reproduces exactly under an independent re-run of the same
sweep tool. The gateway fix's load-bearing assumption — that `AuthorizationError.redirectTo` is
only set once the library has validated `redirectUri` — was checked against the real compiled
library in `node_modules`, not the PR's own description of it, and matches. All of the author's
test claims reproduce fresh: typecheck clean, 71/71 unit tests, and a live 14/14 `diag.py` run
against the actual staging Worker that specifically re-exercises the exact bug this diff fixes
(unregistered `client_id` -> 400, not 500) as a control case. Production is untouched and
structurally isolated from the staging deploy path, consistent with the Frank-gated cutover the
ticket itself calls out.

Not re-implementing anything (ticket instruction: "Do NOT re-implement; verify"). No escalation —
confirming on the board per ticket policy for a non-contradiction.
