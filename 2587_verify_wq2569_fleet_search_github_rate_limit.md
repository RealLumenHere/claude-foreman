# wq#2587 — verify of wq#2569 (PR#131, commit 035778c, lumen-bin repo)

Verifier: claude-sonnet-5@high, fresh instance (same model as author). Verdict:
**CONTRADICT** (one blocking finding; everything else confirmed). Escalated per
archetype 8 to opus-5@high — **wq#2591** filed, `deps=2569`. Full ISSUE-LOG entry: #709.

Note on repo location: wq#2569's deliverable is `fleet-search` in `lumen-bin`
(`/home/lumen/bin`), not this (`foreman`) repo — same cross-repo dispatch quirk logged
as ISSUE-LOG #708 by wq#2569's own author. PR#131 is not yet merged
(`fix/wq2569-fleet-search-github-token`, one commit `035778c` ahead of `lumen-bin`
main). Verified by diffing blob contents (`git show 035778c:fleet-search` vs
`git show main:fleet-search`) and running the shipped test file directly against the
diffed source, copied into a scratch layout under `/tmp/fworker-wq2587/probe/` (no
writes made to `/home/lumen/bin`, which is not my worktree).

## Per claim

| wq#2569 claim | Result | Evidence |
|---|---|---|
| `cmd_github` resolves a token via the shared credential helper, never `agent/.env`/process env directly | **CONFIRMED** | `_resolve_github_token()` (`fleet-search:78-96` in the diff) shells out to `bash -c` running `lumen-github-identity.sh`'s `resolve_github_token`, reads back only `GITHUB_TOKEN`+`TOKEN_SOURCE` over a pipe. `test_resolve_github_token_never_reads_process_env_for_the_value` passes: a poisoned `os.environ["GITHUB_TOKEN"]` has zero effect on the returned token. |
| Only a token-file path crosses back, never the token logged/URL'd | **CONFIRMED** | Read the full diff; no `print`/`log`/URL embedding of `token` anywhere, only `token_source` (a path) is emitted in the final envelope. |
| 401 is reported with the credential-helper wording, not the old `agent/.env` wording | **CONFIRMED** | `fleet-search:389-393`; `test_401_error_names_credential_helper_not_agent_env` passes. |
| 403/429 returns `ok:false` with a formatted reset time | **CONFIRMED** | `fleet-search:398-403`, `_rate_limit_note()` correctly formats a unix timestamp to ISO8601. `test_403_rate_limited_returns_ok_false_with_reset` passes. |
| Test suite: 10/10 pass | **CONFIRMED, independently re-run** | Copied `fleet-search` (at `035778c`) + `tests/test_github_search_auth_rate_limit.py` into a scratch dir, ran `python3 -m pytest -v` directly (not trusting the PR body's claim) — 10 passed, 0 failed, matching the PR's test plan exactly. |

## Blocking finding: `remaining=="0"` discards real, successful results, not just the empty-list symptom

`fleet-search:398` (diff line, `cmd_github`):

```python
if status in (403, 429) or (rl or {}).get("remaining") == "0":
    emit(envelope(kind, False, error=(f"github {status or 200} — rate limited ..."), ...))
```

The PR's own comment states the intent narrowly: "a bare status check misses the case
where the SAME exhausted budget answers 200 with an empty result set." That specific
case (wq#2564's symptom) is real and the fix for it is sound — confirmed by
`test_200_with_remaining_zero_returns_ok_false_not_empty_ok_true`, which only exercises
`items: []`.

But GitHub's `X-RateLimit-Remaining: 0` means "this was the last request your budget
still allowed" — the request that reports it still succeeds and can carry real data.
The guard above does not check whether `data.get("items")` is actually empty; it fires
on remaining==0 unconditionally, so a **successful call with real results** on the
budget's last permitted request is discarded exactly like the empty-list case.

Live-probed against the exact diffed source (not a hypothetical — ran it):

```
$ python3 -m pytest tests/test_wq2587_adversarial_probe.py -v
...
AssertionError: real results with remaining=0 were discarded as a rate-limit failure
(exit=2, ok=False, error='github 200 — rate limited (X-RateLimit-Remaining=0, resets 2025-01-01T00:00:00Z)')

Captured envelope:
{"source": "github-repos", "ok": false, "query": "q", "authenticated": true,
 "rate_limit_reset": "2025-01-01T00:00:00Z",
 "error": "github 200 — rate limited (X-RateLimit-Remaining=0, resets 2025-01-01T00:00:00Z)"}
```

Input to `_get_json_with_headers` was mocked as `(200, {"items": [<one real repo>],
"total_count": 1}, None, {"remaining": "0", "reset": "1735689600"})` — a textbook
successful search response. The fix throws it away.

This is a real-traffic scenario, not an edge case: GitHub's Search API has a low
per-minute cap (30/min authenticated, 10/min for code search) independent of the
5000/hr primary limit, so a fworker doing a short burst of searches will routinely
land its last call of a window at `remaining=0` while still getting valid data back.
Under this fix, that call's real results are silently replaced with a
`rate limited` error — worse than the pre-fix behavior for that specific request,
which returned the real content unmarked (only the *empty*-result case was mislabeled
before; now a *populated*-result case can be mislabeled too).

Scope of the contradiction: narrow. The token-resolution work, the 401/403/429
handling, and the original empty-list wq#2564 fix are all sound and independently
reproduced. Only the `remaining=="0"` branch's missing `not data.get("items")` (or
equivalent) guard is wrong.

## Attempts to break the fix that did NOT find a defect

- `_resolve_github_token`'s subprocess arg order (`bash -c script "fleet-search-github"
  GITHUB_IDENTITY_LIB ""`) looks unusual — `$0` becomes the label, `$1` becomes the
  identity-lib path, `$2` (empty) becomes `resolve_github_token`'s slug arg. Checked
  `resolve_github_token`'s contract (`lumen-github-identity.sh:55`, takes an optional
  `<owner/repo>` slug for a per-repo identity-map override): passing `""` is the
  documented "no override, use default identity" call, and `$0` as a descriptive
  process label is ordinary bash practice for `ps`/debugging. Not a defect.
- Malformed/timeout subprocess paths (`OSError`, `TimeoutExpired`,
  `SubprocessError`, non-zero exit) all fall through to `("", "")` per
  `test_resolve_github_token_empty_on_nonzero_exit` / `..._helper_missing` — confirmed
  by inspection, matches.
- 422 and generic non-200 paths (`fleet-search:404-407`) are ordered after the
  403/429/remaining-0 check and before the generic fallback — order is correct, no
  branch is unreachable or double-emits (every branch calls `emit()`, which
  `sys.exit()`s, so there's no accidental fallthrough).
- discussions/GraphQL path (`getattr(args, "discussions", False)`) is unaffected by
  this diff's rate-limit changes; separately confirmed its own token-missing path
  emits a clear error rather than crashing on `None` token.

## Actions taken

| Action | Rationale |
|---|---|
| ISSUE-LOG **#709** appended | Records the false-positive discard and the missing test case, per doctrine |
| **wq#2591** filed, pri 1, `deps=2569`, `claude-opus-5@high` | Judgment ticket per archetype-8 protocol: decide fast-follow spec (gate the check on `not data.get("items")`/`total_count==0` in addition to `remaining==0`) vs. reopen wq#2569 |
| wq#2569 left `done`/PR#131 left open, not reopened or edited here | Verify-only per archetype 8 — do not re-implement; opus-5@high judgment decides disposition |
