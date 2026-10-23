# wq#4621 verify of wq#4616 — Cloudflare Access login-redirect `meta` JWT burn-filter screen

- Commit: `3ef340cd` (`fix(burn-filter): account Cloudflare Access login meta JWTs`),
  already merged to `main`. Gitea PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2453
  (MCP read-access to gitea unavailable this run — `mcp__gitea__*` is not in
  `harness/worker-settings.json`'s allowlist for this worker and the public API 404'd
  unauthenticated — so verification is against the merged commit content directly,
  which is the authoritative artifact).
- Author model: claude-opus-5-5. Verifier: claude-sonnet-5@high, fresh instance.
- Verdict: **CONFIRM**. The mechanism, the fix, and the probes are all correct. No
  escalation to opus-5@high.

## What the PR claims

wq#4607's worker curled the Access-protected `ntfy.lumennode.uk` feed unauthenticated,
got 302'd to `<team>.cloudflareaccess.com/cdn-cgi/access/login/<host>?meta=<jwt>`, and the
`jwt` mask label filed wq#4616 as a pri-1 rotation because the token had no tree
provenance. The `meta` JWT is Cloudflare's own signed envelope of the bounced request
(`type: meta`, `hostname`, `redirect_url`, `auth_status`, a 300s `exp`, no identity
claims) — issued to any anonymous client, authorizes nothing, and a fresh one is minted
on every curl. Fix: `burn_filter.is_access_login_meta()` decodes the JWT's claims
in-process (no argv, no `git grep`) and accounts the token as benign only if `type ==
"meta"`, every one of `hostname`/`redirect_url`/`auth_status`/`exp`/`iat` is present,
none of `sub`/`email`/`identity_nonce`/`common_name`/`custom`/`groups` is present, and
`0 < exp - iat <= 900`. Anything else (type app/org — the real `CF_Authorization`
session token/service token, an identity claim, a long-lived or unparseable `exp`,
undecodable base64/JSON) still screens as burned. `mask_replay.py`'s `accounted_in`
gains a `"cloudflare-access-login-meta"` fallback label for the same test.
`rotated_ledger.json` records wq4616 as **NOT ROTATED**, with a note that a fresh-fetch
probe against the live endpoint reproduced the claim shape claim-by-claim.

## What I checked

- Read the full diff cold: `harness/burn_filter.py` +47 (new `_jwt_claims` /
  `is_access_login_meta`, wired into `_is_accounted` before the provenance checks),
  `harness/mask_replay.py` +4/-1 (one new `accounted_in` branch), `harness/tests/
  test_burn_filter.py` +60 (7 new tests). Confirmed via `git show --stat 3ef340cd` this
  is the complete scope — no other files touched.
- Confirmed the `jwt` masking rule (`harness/masking.py:582`) has an **empty** fixed
  prefix (group 1 is `()`), so `Hit.body == Hit` (the whole match) for this label —
  `is_access_login_meta(match)` always receives the bare three-segment JWT, never a
  `meta=` query-string prefix glued on. Ruled out a prefix-contamination bug before
  looking anywhere else.
- Traced `classify()`: the `live` secret-set check (`any(secret in match for match in
  matches for secret in live)`) runs **before** `_is_accounted`/`is_access_login_meta`,
  so a token that is also a tracked live secret is burned regardless of its claims —
  `is_access_login_meta` can only excuse a token nothing else already flags as live.
  Verified by `test_live_access_meta_is_still_burned` (passes).
- Ran the ticket's own acceptance probe exactly as pinned in `_runs/wq4616/
  acceptance.json`: `python3 /home/lumen/foreman/harness/mask_replay.py 4607` → exit 0,
  `[ROTATED]` line shows `accounted: cloudflare-access-login-meta`, `rotated:
  2026-10-23T20:30:53Z (wq4616)` — matches the ledger.
- Ran the targeted suite: `python3 -m pytest harness/tests/test_burn_filter.py` → 54
  passed, 1 skipped (pre-existing skip, unrelated).
- Ran the broader `test_mask_replay.py` suite → 96 passed, 2 failed, 25 skipped. Both
  failures are **pre-existing, documented, unrelated drift**, not caused by this diff:
  `test_wq2253_sk_key_is_absent_from_the_model_context` pins a line-numbered read into
  the live, constantly-mutated `ISSUE-LOG.md` (ISSUE-LOG.md:1214, row 1066, open since
  2026-09-29); `test_wq2278_unaccounted_values_are_fixtures_off_its_own_branch` pins a
  fixed fixture map against a live corpus that drifts with no code change
  (ISSUE-LOG.md:1579, row 1430, same failure already caught and dismissed as unrelated
  during wq#4255's verify of wq#4251). Neither test touches `burn_filter.py` or the
  `jwt`/Cloudflare path.
- **Falsifiability check** (wq#2630): in my own worktree copy only (never the shared
  `/home/lumen/foreman` checkout other lanes read), deleted the identity-claim guard
  (`if any(k in claims for k in _IDENTITY_CLAIMS): return False`) from
  `is_access_login_meta` and reran the targeted tests. Caught immediately: 3 of 7
  `test_access_meta_screen_fails_toward_burned` parametrizations failed
  (`email`/`sub`/`common_name` cases wrongly turned benign), proving the identity-claim
  check is load-bearing, not tautological. Restored the two lines; `git status` on the
  file is clean again and the full targeted suite (54 passed, 1 skipped) passes.
- Independent adversarial probing beyond the PR's own tests (built at runtime, never a
  literal token, via the same `_access_jwt`-style helper): `exp - iat == 900` → benign
  (boundary inclusive, matches `<=`); `901` → burned; missing `exp` or `iat` entirely →
  burned; missing the `type` key entirely → burned; `email: ""` (empty-string identity
  claim) → burned (presence alone excludes, not truthiness); a 2-part token, a JSON
  array payload, and un-decodable base64 garbage → all burned (fail safe on decode
  failure). All matched the fix's intended fail-toward-burned direction.
- Grepped for anywhere else in the tree that reads `jwt`-labeled matches or
  `accounted_in` that this change might have silently altered: only `mask_replay.py`'s
  one new branch and `burn_filter.classify`'s existing `_is_accounted` call site: no
  other caller.

## Verdict

CONFIRM. No contradiction found.
