# wq#1884 — Adversarial verify-pass of wq#1852 (despag: 3 UHP correctness gaps)

**Verdict: CONFIRM.** PR #558 (branch `fix/wq1852-...`, commit `bae1e17`, merged to `main`)
does what it claims for all three named bugs. Read the diff cold, reproduced the pre-fix
failure mode directly, ran the full existing regression suite, and read the two changed
call sites (`errors.py`, `app.py`) against the ticket's own repro steps.

## What was checked

Diff: `despag/app.py` (+21/-3), `despag/uhp/errors.py` (+6/-3),
`despag/tests/test_uhp.py` (+44), plus a committed `uhp_conformance` core-class run
(`despag/reports/wq1852_core_postfix.json`).

- **Gap 1 — `TYPE_STATUS` missing `harness_error`**: `CODE_TYPES` types
  `harness_error`/`provider_error`/`timeout` all as `"harness_error"`, but `TYPE_STATUS`
  had no such key, so `error_response()` did `TYPE_STATUS[err_type]` → `KeyError`.
  Reproduced directly: checked out `bae1e17~1`'s `errors.py` in isolation and called
  `error_response("provider_error", "boom")` — raises `KeyError: 'harness_error'` for
  all three codes, exactly as the ticket describes. Post-fix, all three return
  `status_code=200` with `type="harness_error"`, `code=<original>` — matches
  `despag/tiers.py:131-132`'s own independent comment that harness_error is spec'd as
  HTTP 200 embedded in a `status: "failed"` response (errors.md §1-2). No caller in
  `app.py` currently invokes `error_response()` with these three codes (harness
  failures are built as raw dicts in `tiers.py`/`translate.py`, bypassing this table
  entirely) — the ticket itself flags this as "unconditionally reachable by the next
  ticket that wires a harness failure through this table," i.e. a latent-but-real gap
  in a closed code table, not a currently-exercised path. Fixing it now is correct;
  it is not a fabricated bug.

- **Gap 2 — `/v1/models` reports a shared model id twice**: `chrn_claude-max` and the
  disabled `chrn_claude-local-rung` both default to `claude-sonnet-5`; pre-fix,
  `list_models()` appended one entry per harness with no coalescing, so the response
  held `claude-sonnet-5` twice (`available:true` and `available:false`). Post-fix
  `seen: dict[(backend, model_id), entry]` coalesces on first sight and ORs
  `available`/`default` across harnesses on repeat sight (`app.py:444-457`) — matches
  the ticket's requested semantic ("available if ANY enabled harness serving it is
  available"). Read `_model_entry`/`_harness_model_ids` to confirm the dedup key
  (`backend`, `model_id`) is the actual identity the bug report used, not a narrower
  or looser key that would coalesce distinct models. Bucket-level `"default"` (a
  separate, backend-wide field predating this PR) is unchanged and out of scope.

- **Gap 3 — version-negotiation rejection missing `UHP-Version` header**: the
  middleware's early-return branch (`app.py:341-352`) built `error_response(...)` and
  returned it directly, never reaching the `response.headers["UHP-Version"] = ...`
  line that runs after `call_next()`. Fix stamps the header on `rejected` before
  returning. Diff is a 2-line, unambiguous fix; test added exercises exactly this
  branch (`c.get("/v1/uhp", headers={"UHP-Version": "1999-01-01"})`) plus the
  pre-existing authenticated-401 path, both asserting the header is present.

## Regression run

`python3 -m pytest despag/tests/` — **139/139 PASS** (30 in `test_uhp.py` alone, up
from the ticket-cited 13 pre-fix — 17 new assertions/tests, not just the 3 for this
PR's own bugs).

## Adversarially probed and found sound

- Coalescing order-dependence: `seen` keys on `(backend, model_id)`, so a `litellm`
  and a `claude` backend never cross-coalesce even if a model id string collided
  (it can't here — `_is_local_model` partitions ids by backend deterministically).
- The `TYPE_STATUS["harness_error"] = 200` entry does not shadow or change any
  existing `CODE_STATUS_OVERRIDE` entry — checked the full table, no other code maps
  to type `harness_error` besides the three named ones, so this is additive only.

## Known issue found (not a defect in wq#1852 — logged separately)

`despag/reports/wq1852_core_postfix.json` (the PR's own committed conformance
evidence) shows **38/40** on the core `uhp_conformance` suite: `T-08` ("a request
carrying reserved fields is accepted, not rejected") **fails** — "the task carrying
reserved fields ended 'failed'". The done-notes call this "pre-existing," and
`kb/ISSUE-LOG.md` #372 (wq#1852's own attempt log) corroborates it first-hand:
"Conformance ran clean: 38/40 core, identical before and after the fix" — i.e. they
ran the suite once before touching code and once after, got the same 38/40 both
times. But the last *clean* core run committed to the repo before that,
`despag/reports/p1-3_core_claudemax.json` (`926416f`, same day, ~10h before
wq#1852 claimed the ticket), shows **T-08 passing at 40/40**. So the regression
window is real and narrow: between `926416f` (15:56) and wq#1852's claim
(01:53 the next cycle) the only despag-touching change is `2fe67a9` (wq#1812 P1-7
account-global Max admission gate, landed 16:15, which also touched
`despag/runner/turn.py`'s turn-completion path) — the leading suspect, though not
directly confirmed against a T-08 repro of my own (no grant for
`dir(/home/lumen/research/harnessrouter-ref)` in this verify ticket's `needs`, so I
could not independently re-run the official CLI — same structural gap as
ISSUE-LOG #361). wq#1852's own diff (`errors.py`, `app.py`'s version-header and
model-catalog code) does not touch admission, task execution, or reserved-field
handling at all, so **wq#1852 did not cause this** regardless of exact origin.
Filed as a new follow-up ticket rather than blocking this verify-pass; logged to
`kb/ISSUE-LOG.md` #374.

## Conclusion

All three claimed fixes are real, correctly implemented, and covered by tests that
would fail against the pre-fix code (confirmed by direct reproduction for gap 1 and
by reading the exact branch each new test exercises for gaps 2-3). Full regression
suite green at 139/139. No contradiction found in the diff under review. The T-08
conformance regression is real but out of scope — a separate, already-in-progress
concern at the time wq#1852 started, not introduced by it.
