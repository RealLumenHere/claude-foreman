# wq#1443 — verify-pass: wq#1434 fix for wq#1373 cf-token-liveness uncaught KeyError

**Verdict: CONFIRM** — the fix contains the crash class wq#1433 reported, gives
the pass-level abort path a distinct, non-whitelisted exit code, and
correctly keeps checking later manifest entries after a malformed one. One
minor, non-blocking diagnostic-labeling edge case is noted below; it does not
reopen the crash/skip/mask failure mode this ticket exists to close.

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/312 (merged,
`349b498`, branch `feat/wq1434-judgment-wq-1373-cf-token-liveness-fix-u-a3`).

## What was verified

1. **The reported crash is gone.** Re-ran the *pre-fix* script (still on
   local `/home/lumen/foreman` main, one commit behind `origin/main`) against
   a two-entry manifest where the first entry omits `account_id_file`:
   uncaught `KeyError: 'account_id_file'` out of `probe_d1`, exit 1, second
   (healthy-shaped) entry never checked — reproduces wq#1433 exactly.
   Ran the *post-fix* script (`origin/main:ops/wq1373-cf-token-liveness/cf_token_liveness.py`,
   copied out via `git show` since this worktree branched before the merge)
   against the identical manifest:
   ```
   [dry-run] would alert priority=urgent title='CF token liveness: DEAD token(s)'
   broken-entry-missing-key: malformed manifest entry: KeyError: 'account_id_file'
   second-entry-should-still-run: d1 probe failed: HTTP Error 401: Unauthorized
   cf_token_liveness: 0/2 tokens live — FAILED: ...
   EXIT=1
   ```
   Both entries are now checked, the urgent alert fires, and the process
   exits 1 (a per-token failure) instead of crashing uncaught.

2. **Non-dict manifest entries** (`"tokens": ["oops", {...}]`) are also
   caught (`AttributeError: 'str' object has no attribute 'get'`) and the
   loop still reaches the following good entry — matches the
   `CONFIG_ERRORS = (KeyError, AttributeError, TypeError, ValueError)` design
   comment at `cf_token_liveness.py:57-61`.

3. **Pass-level abort path.** A manifest with no `tokens` array
   (`{"not_tokens": []}`) now raises inside `run()`, is caught by `main()`'s
   backstop, prints a traceback + `[dry-run] ... monitor ABORTED` alert, and
   exits **2** — distinct from the exit-1 per-token-failure path, and not in
   `cf-token-liveness.service`'s `SuccessExitStatus=0 1`, so a real crash now
   fails the unit and skips the `ExecStartPost=` stamp (`lumen-cron-deadman`,
   wq#1184, would flag it as stale). Confirmed the unit file's comment
   matches this behavior exactly.

4. **No regression against the real manifest.** Ran the post-fix script
   against the live `ops/wq1373-cf-token-liveness/tokens.json` (real
   `/home/lumen/mailbot/.cf_d1_token` / `.cf_d1_write_token` files, read but
   never printed, same read-only `SELECT 1` D1 call the production timer
   already makes every cycle): `cf_token_liveness: 2/2 tokens live`, exit 0 —
   matches wq#1434's own closing note.

5. **Citation check.** `evidence.json`'s `AC1` citation
   (`cf_token_liveness.py:142-174`) is `check_one()` — the function actually
   changed to fix the reported bug. Accurate.

## Minor observation (not a contradiction)

`CONFIG_ERRORS` includes `ValueError`, which also covers
`json.JSONDecodeError`. Monkey-patched `urllib.request.urlopen` to return a
non-JSON 2xx body (simulating a CF-side WAF/outage page) and called
`check_one()` directly: the resulting `json.JSONDecodeError` from
`_cf_get`'s `json.load(r)` is caught by the outer `except CONFIG_ERRORS`
(not the inner probe-failure handler), so the alert reads `"malformed
manifest entry: JSONDecodeError: ..."` instead of `"... probe failed: ..."`.
This mislabels a live API/network response issue as a manifest config
problem.

This is **not** a repeat of wq#1433's failure mode: the alert still fires at
`urgent`, the exit code is still 1, and the loop still continues to later
entries — the only cost is a slightly misleading label during an edge case
(Cloudflare returning a 2xx with a non-JSON body) that wq#1433's own
disposition section explicitly named as in-scope for this catch-all
(`"json.JSONDecodeError`/`ValueError` for a non-JSON CF response body ...
as a per-token failure"`) — which is exactly what happens here. Worth a label
tweak (distinguish response-parse errors from manifest-shape errors) in a
future pass, not worth blocking this fix on.

## Disposition

Confirmed. The three failure classes wq#1434 was scoped to fix (config-shape
`KeyError`, a distinct crash-path exit code, and continuing past a bad entry)
are all demonstrated fixed, live, against both synthetic and real manifests.
Not re-implementing anything (verify-pass, not a fix ticket).
