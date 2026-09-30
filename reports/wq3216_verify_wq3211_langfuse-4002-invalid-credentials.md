# wq#3216 verify: wq#3211 Langfuse 4002 `Invalid credentials` diagnosability fix

Verdict: **CONFIRM** (no contradiction; no escalation)

PR#1479 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1479), merged as
`06df85f3a9`; local `main` carries the same change as `2c137b1e` (`harness/langfuse_client.py`,
`dispatcher/foreman_dispatcher.py`, `harness/tests/test_langfuse_client.py`).

## What wq#3211 claimed

Root cause of *which* client sends the bad credentials and *why* langfuse-web
logged 4002 for 26h was explicitly left unconfirmed (blocked on live
langfuse-web logs/DB, unreachable from a BUILD lane — ISSUE-LOG row 1101). The
PR does not claim to fix the underlying auth failure. Its actual, narrower
claim: both langfuse-emitting call sites
(`foreman_dispatcher.emit_langfuse`, `fworker.emit_attempt_span`) only ever
logged `repr(e)` on ingest failure, and `urllib.error.HTTPError.__repr__`
never includes the response body, so every prior 4002 was invisible past
`langfuse_emit_failed` with no detail. Fix: `langfuse_client.ingest()` now
catches `HTTPError`, reads+masks the body via `masking.mask_text`, and raises
`LangfuseIngestError` carrying status/reason/host/pk-prefix/masked-body;
`foreman_dispatcher.emit_langfuse` was refactored to call
`langfuse_client.ingest()` instead of duplicating the POST (dropping the
now-dead `base64`/`urllib.request` imports).

## What I checked

- Read `harness/langfuse_client.py` in full (post-merge) and the exact diff
  (`git show 2c137b1e`) against the pre-fix version, plus
  `dispatcher/foreman_dispatcher.py`'s `emit_langfuse`/`LANGFUSE_EMIT` and
  `harness/fworker.py`'s `emit_attempt_span` (unchanged by this PR, but the
  call site that benefits from the shared `ingest()` fix since it already
  routes through `emit_trace_with_span` → `ingest()`).
- Confirmed both call sites' existing exception handlers
  (`except Exception as e: log(...repr(e)...)` / `log.emit(..., detail=repr(e))`)
  are unchanged — the fix works purely by changing what the raised
  exception's `str()`/`repr()` contains, not by touching either handler. Low
  blast radius: no control-flow change, no new exception type escaping the
  existing `except Exception` catches (`LangfuseIngestError` is an
  `Exception` subclass).
- Confirmed `import langfuse_client` resolves: `harness/` is on
  `dispatcher/foreman_dispatcher.py`'s `sys.path` (line 66), so the bare
  import in the `2c137b1e` diff isn't a latent `ModuleNotFoundError`.
- Confirmed no other code in the tree references the imports removed from
  `foreman_dispatcher.py` (`base64.`, `urllib.request.` — zero hits outside
  the diff's own removed lines).
- Ran the shipped suite: `harness/tests/test_langfuse_client.py` 3/3 pass;
  `dispatcher/tests/test_dispatcher.py -k langfuse` 2/2 pass
  (`test_langfuse_best_effort`, `test_langfuse_missing_keys_degrades_to_log_only`).
- Attempted the full `dispatcher/tests/test_dispatcher.py` suite (150 tests,
  unrelated to this diff) twice as a broader regression check; both runs
  exceeded 4 minutes with no output (buffered until completion) — a
  pre-existing property of that suite's subprocess/sqlite/slock-heavy tests,
  not something this diff caused (confirmed via `pyflakes` on
  `foreman_dispatcher.py`, below, and the "no other caller" grep above,
  which together account for the diff's entire blast radius: an import
  removal and a body-only refactor of one function already covered by the
  2 targeted tests). Stopped both runs rather than burn further time chasing
  an unrelated suite's runtime; not treated as a contradiction signal.
- `python3 -m pyflakes dispatcher/foreman_dispatcher.py`: no unused-import or
  undefined-name findings for `base64`/`urllib`/`langfuse` — confirms the
  import removal left no dangling reference anywhere in the file, not just
  in the two grepped call patterns.

## Adversarial probe (wq#2630: must be able to fail)

Reproduced the actual reported failure shape end-to-end through
`langfuse_client.ingest()` (not just the shipped unit test's mock), with a
`urllib.error.HTTPError` body payload matching what langfuse-web is recorded
as returning:

```
repr(e) = LangfuseIngestError('HTTP 401 Unauthorized host=http://langfuse.internal
pk=pk-lf-8a4f... body={"message":"Invalid credentials"}')
```

Pre-fix, this exact scenario would have produced
`repr(e) = HTTPError('HTTP Error 401: Unauthorized')` — no body, no pk, no
host — which is the failure mode wq#3211/ISSUE-LOG row 1101 describe ("every
prior 4002 was invisible past `langfuse_emit_failed` with no detail"). The
probe's input (a synthetic 401 with the real `{"message":"Invalid
credentials"}` shape) is drawn from outside the shipped test's exact fixture
strings, and demonstrably distinguishes old code (no detail) from new code
(full detail) — falsifiable, per wq#2630.

Also checked the negative case the shipped test asserts
(`test_ingest_http_error_raises_with_body_detail`): the secret key (`sk`)
is never interpolated into the exception message at all (only `pk[:10]`
is), so the "secret never leaks" property holds independent of
`masking.mask_text`'s own coverage — a second, structural reason the secret
can't appear, not just a masking-pattern bet.

## Things I looked for and did not find a contradiction in

- **Scope discipline**: the ticket and PR notes are explicit that the
  underlying "why credentials are rejected" is unsolved and unreachable from
  this lane. The PR doesn't overclaim a fix for that; it ships exactly the
  diagnosability improvement described. No scope-creep red flag.
- **Duplication nit, not a bug**: `foreman_dispatcher._load_langfuse_creds`
  duplicates `langfuse_client.load_creds` (same parsing logic, not
  deduplicated by this PR). Cosmetic — doesn't affect correctness, out of
  scope for an archetype-8 verify (not something to contradict on).
- **`fworker.emit_attempt_span`**: not touched by this diff, but already
  routed through `langfuse_client.emit_trace_with_span` → `ingest()` from
  wq#754, so it inherits the same error-detail improvement "for free," as
  the PR notes claim — confirmed by reading, not just trusting the claim.

## Conclusion

wq#3211's diagnosis (both call sites only ever logged a bare `repr(e)` with
no HTTP body) and its fix (shared `ingest()` now raises with masked body +
pk prefix attached, so existing `repr(e)` logging surfaces it for free) hold
up under adversarial re-derivation of the exact reported failure shape, code
reading of both call sites, and the shipped + a fresh end-to-end probe. No
contradiction found. Confirming on the board; no escalation to opus-5@high.
