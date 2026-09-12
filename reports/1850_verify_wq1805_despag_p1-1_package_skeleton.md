# wq#1850 — Adversarial verify-pass of wq#1805 (despag P1-1: package skeleton + UHP discovery/harnesses/models)

**Verdict: CONTRADICT** — the shipped diff (`4230e60` `feat(despag): wq1805 p1-1 uhp
discovery skeleton`, merged to `main`, HEAD of `/home/lumen/foreman` at review time)
correctly delivers what its own report/tests measure (18/18 conformance checks, 13/13
pytest, both reproduced live below), but three real, independently-reproduced defects
sit outside what either suite exercises. One of them (`error_response()` crashes on 3 of
its own "closed code table" entries) is unconditionally reachable by any future caller,
not an edge case.

## What was shipped

`despag/` package: `uhp/models.py` (generated pydantic v2 models from the UHP OpenAPI
schema via the committed `tools/gen_uhp_models.py`), `harnesses.yaml` (3 static harness
objects), `app.py` (unauthenticated `GET /v1/uhp` discovery, UHP-Version negotiation
middleware, bearer auth on every other route, `/v1/harnesses`, `/v1/models`,
`/v1/harnesses/{id}/models`, `/v1/responses` 501), `uhp/errors.py` (single error
envelope + closed code table), `despag/reports/p1-1_discovery.json` (committed
conformance report claiming 18/18 pass), `despag/tests/test_uhp.py` (13 pytest cases).

## Method — ran it, not just read it

- `cd /home/lumen/worktrees/wq1850-a1 && python3 -m pytest despag/tests/ -v` →
  **13 passed**, matches wq#1805's notes exactly.
- Started a live instance (`python3 -m despag`, scratch token file, alt port — the
  default `~/.secrets/despag-api.token` path and `DESPAG_PORT=8790` are deploy
  conventions, not touched) and drove it with `curl`, including cases neither the
  pytest suite nor (as far as the committed report's check titles indicate) the official
  conformance run exercises.
- Could **not** re-run the official `uhp_conformance` CLI against the live instance:
  the acceptance command in wq#1805's own ticket text (`cd
  /home/lumen/research/harnessrouter-ref/... && python3 -m uhp_conformance.cli ...`)
  requires a directory this verify ticket was never granted (`needs` on wq#1850 is only
  `ssh(anubis)`, added by capability_lint; wq#1805's `needs` explicitly listed `dir(
  /home/lumen/research/harnessrouter-ref)` and three subpaths, which wq#1850 did not
  inherit). `ssh anubis` itself is also refused ("This command requires approval") —
  same class of gap as ISSUE-LOG #256/#307 (verify tickets not inheriting the author
  ticket's directory grants for re-running its own acceptance probe). Logged as
  ISSUE-LOG #(see below); did not retry (wq#1107 loop-breaker lesson). Treated the
  committed `despag/reports/p1-1_discovery.json` as evidence of what the real suite
  found, and independently checked every claim in it I could reach by other means
  (pytest, direct HTTP, direct Python import) instead.

## Claim-by-claim check

1. **13/13 pytest, 18/18 conformance, as claimed** — pytest reproduced exactly (13
   passed, 0 failed). The committed report's structure and specific numbers
   (`H-03: "0/3 models available"`) are internally consistent with what a live probe of
   this exact `harnesses.yaml` produces (see finding 2 below — 3 total model entries
   really is what `/v1/models` returns, duplicate included), so the report reads as
   genuine output, not fabricated.

2. **FINDING — `/v1/models` can report the same model id as both available and
   unavailable in one response** (`despag/app.py:186-197`, `_model_entry` at :179-183).
   `list_models()` builds the catalog by iterating every harness and appending one
   model entry per harness, keyed by nothing — it never dedupes by model id.
   `chrn_claude-max` (enabled) and `chrn_claude-local-rung` (disabled) both have
   `defaultModel: claude-sonnet-5` (`despag/harnesses.yaml:15,29`). Reproduced live: with
   the real `DESPAG_CLAUDE_BIN` swapped for a stub that reports `{"loggedIn": true}` (so
   `compute_available` would say the *model* is reachable), `GET /v1/models` returned:
   ```
   "claude": {"default": "claude-sonnet-5", "models": [
     {"id": "claude-sonnet-5", ..., "available": true,  "default": true},
     {"id": "claude-sonnet-5", ..., "available": false, "default": true}
   ]}
   ```
   The same model id, in the same response, reported both available and not — because
   `compute_available()` short-circuits on `enabled: false` for the disabled harness
   (:102-103) before it ever reaches the shared-model check. `test_disabled_harness_never_available`
   only asserts `compute_available(h)` in isolation for the disabled harness; nothing
   exercises `/v1/models` with two harnesses sharing a `defaultModel`, so this shipped
   uncaught. In the current dev environment (`0/3 models available`, per the committed
   report) both entries happen to read `false`, which is why the official run didn't
   surface the contradiction — it only appears once `claude auth status` reports
   `loggedIn`.

3. **FINDING — `error_response()` raises an unhandled `KeyError` for 3 of its own closed
   code-table entries** (`despag/uhp/errors.py:13-19,22-47,81`). `CODE_TYPES` maps
   `harness_error`, `provider_error`, and `timeout` to the type string `"harness_error"`
   (:40,42-43), but `TYPE_STATUS` (:13-19) never defines a `"harness_error"` key, and
   `CODE_STATUS_OVERRIDE` (:50-66) has no entry for any of the three codes either. Line
   81's fallback `TYPE_STATUS[err_type]` then does a bare dict lookup on a key that does
   not exist. Reproduced directly:
   ```
   >>> from despag.uhp.errors import error_response
   >>> error_response("provider_error", "boom")
   KeyError: 'harness_error'
   ```
   Same crash for `"harness_error"` and `"timeout"`. None of the 13 shipped pytest cases
   call these three codes (`test_closed_code_table_status_codes` only exercises
   `harness_not_found`, `session_busy`, `rate_limited`) and P1-1's own routes never emit
   them either (task execution is out of scope here), so this is currently dormant —
   but it is unconditionally reachable by the very next ticket that wires a harness
   failure path through this "closed code table," which is the whole point of the
   abstraction (callers pass a code, not a status). A table that crashes on 3 of its ~20
   entries isn't closed in the sense errors.md intends.

4. **FINDING — the UHP-Version response header is missing on exactly the response the
   negotiation feature is supposed to produce** (`despag/app.py:113-124`). The
   version-negotiation middleware sets `response.headers["UHP-Version"]` only after
   `call_next(request)` returns (:122-123); its early-return branch for an unsupported
   version (:116-121) returns `error_response(...)` directly, bypassing that line.
   Reproduced live:
   ```
   $ curl -i http://127.0.0.1:8791/v1/uhp -H "UHP-Version: 1999-01-01"
   HTTP/1.1 400 Bad Request
   content-type: application/json
   {"error":{"type":"invalid_request_error","code":"unsupported_protocol_version",...}}
   ```
   No `uhp-version:` header — every other response observed (200s, 401s, 404s, the
   501) carries it, because those all flow through `call_next`. The pytest suite has a
   test literally named `test_uhp_version_header_on_every_response` (`despag/tests/
   test_uhp.py:34-37`), but it only calls a plain `GET /v1/uhp` with no `UHP-Version`
   header — it never exercises the one branch where the header is actually at risk of
   being dropped, so the test's name overstates what it checks. wq#1805's own ticket
   text names this exact response ("UHP-Version header negotiation (400
   unsupported_protocol_version listing supported versions)") as an acceptance item.
   Could not confirm whether the official `uhp_conformance` V-01 check
   ("UHP-Version header on every response") would also miss this — did not have
   access to `checks.py` to read what it actually probes (see Method).

## Adversarial angles tried, nothing found

- Case sensitivity / header aliasing on `Authorization` and `UHP-Version` — Starlette's
  header dict is case-insensitive by construction; probed with `Authorization: `
  (empty) and a wrong scheme (`Basic xyz`) — both correctly 401 `missing_credential`/
  `invalid_credential`.
- Unmapped routes (`GET /v1/nonexistent-route`) fall through to FastAPI's default
  `{"detail":"Not Found"}` rather than the custom envelope. Noted, not treated as a
  finding: the ticket's closed code table and 404-not-403 scoping requirement are about
  named object lookups (`harness_not_found`, `response_not_found`), not routing 404s,
  and the conformance suite's own check titles (`H-*`/`E-*`) are all scoped to known
  routes.
- `_litellm_healthy()`'s `finally: conn.close()` guarded by `except NameError` for the
  case `HTTPConnection(...)` itself raises before `conn` is bound — read correctly, no
  bug.
- Token load/creation (`_load_or_create_token`) — file is created 0700 dir / written,
  then explicitly `chmod(0o600)` on every read, matches "0600" requirement even on an
  existing file with looser permissions.
- Bearer comparison in `require_auth` (`despag/app.py:131`) is a plain `!=`, not
  constant-time (`hmac.compare_digest`) — a timing side-channel on the token. Noted,
  not escalated: P1-1 runs loopback-only per its own doc comment, and this is a
  pre-existing pattern class, not unique to this diff.

## Bottom line

CONTRADICT. The measurable claims (13/13 pytest, 18/18 conformance) are honest and
reproduced live. But adversarial probing beyond the shipped tests found three real
defects: a self-contradictory `/v1/models` catalog when two harnesses share a
`defaultModel` (#2), an unconditionally-reachable `KeyError` in the "closed" error code
table for 3 of its entries (#3), and a missing `UHP-Version` header on exactly the
response the version-negotiation feature exists to produce (#4). None are re-implemented
here per the verify-pass archetype — filed as a follow-up fix ticket (wq#1852,
`claude-opus-5@high`, deps on wq#1805) per this archetype's escalation rule.
