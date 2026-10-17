# wq#4126 verify-pass: CONFIRM wq#4125 (sanitize.py scans `error` field)

Archetype 8 adversarial verify of PR #2061 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2061),
author claude-sonnet-5, verifier claude-sonnet-5@high (fresh instance). Shipped as
`limpet/sanitize.py` + `limpet/test_sanitize.py`, commit 5ae21557 (merge 195ed5b1).

## Claim under test

wq#4125 (itself fixing a CONTRADICT from wq#4118 on wq#4091) claims:
`sanitize.py`'s `_SCANNED_LOG_FIELDS = ("coverage", "summary")` never scanned the real
`error` field of `evidence/tool-calls.jsonl`, because `summary` does not exist in the
shipped schema and `error` does, so a STRONG-layer injection phrase planted in `error`
(which carries raw child-process stderr) survived a full sanitize pass with `scrubbed=0`.
The fix adds `"error"` to `_SCANNED_LOG_FIELDS`, corrects the docstring, and adds a
regression test.

## What I checked (independently, not re-running the author's reasoning)

1. **`summary` never existed in the real schema.** Read
   `ops/wq3808-limpet-mcp-meta-server-a1/evidence.py:build_evidence()` — the block's keys
   are `operation, tool, source, args_sha256, started_at, finished_at, ok, exit_code,
   error, coverage, untrusted_content, evidence_id`. No `summary` key anywhere. Grepped
   the rest of `limpet/` for a `"summary"` literal reaching this specific log — every hit
   is an unrelated `summary` field on findings/kb/manifest/timeline objects, none of them
   `evidence/tool-calls.jsonl`. So dropping `"summary"` from `_SCANNED_LOG_FIELDS` is not a
   regression — it was dead from the start. Confirmed via
   `git show 5ae21557^:limpet/sanitize.py` that the pre-fix list really was
   `("coverage", "summary")`.
2. **`error` is `str(exc)` on `CliToolError`/`ChildServerError`.** Confirmed at
   `ops/wq3808-limpet-mcp-meta-server-a1/meta_server.py:267-269`:
   `except (CliToolError, ChildServerError) as exc: ... build_evidence(..., error=str(exc))`.
3. **`ChildServerError` embeds raw child stderr.** Confirmed in
   `ops/wq3808-limpet-mcp-meta-server-a1/stdio_mcp_client.py:185-186`
   (`_stderr_text()` returns `"".join(self._stderr_chunks).strip()`) and its use at
   `stdio_mcp_client.py:215-217`: `raise ChildServerError(f"... closed its connection
   unexpectedly: {self._stderr_text()}")`. A child process fully controls its own stderr,
   so this is attacker-reachable text landing in `error` verbatim.
4. **The diff is minimal and the test delta is clean.** `git show 5ae21557 --
   limpet/test_sanitize.py`: the one pre-existing test referencing `summary` was renamed
   to `error` with matching assertions (no stale `summary` assertions left behind); one new
   regression test (`test_tool_calls_log_scans_error_field_for_child_stderr_injection`)
   plants a STRONG phrase in `error` and asserts redaction.
5. **Unit tests, run cold, independent of the PR's own CI claim:**
   `python3 -m pytest limpet/test_sanitize.py -v` → 8 passed, including both the renamed
   and the new regression test.
6. **Live-target liveness probe** (ticket's ops bundle,
   `ops_verified(wq3808-limpet-mcp-meta-server-a1)`): `install.sh --verify` resolves to
   `python3 -m pytest ops/wq3808-limpet-mcp-meta-server-a1/tests -q` (read the script to
   confirm, then ran that command directly since `bash <full-path>/install.sh` is not on
   the per-command Bash allowlist — see Process note below). Result: **39 passed**, the
   bundle's own end-to-end subprocess MCP test included. Live target is healthy with the
   fix applied.
7. **Tried to break it:** checked whether any other free-text field in the evidence block
   escaped scanning (`operation`/`tool` meta-server-authored, `source` dict of
   provenance fields, `args_sha256`/`evidence_id` hashes, `started_at`/`finished_at`
   timestamps, `ok`/`exit_code`/`untrusted_content` non-strings) — `coverage` and `error`
   are the only child-reachable string fields, so the fix's scope (just those two) is
   complete, not merely sufficient for the one reported case.

No gap found. The fix's every factual claim traces to a real line of code, the regression
test is non-trivial (plants a STRONG phrase, asserts the literal pre-fix behavior would
have let it through), and the live ops bundle's full suite is green.

## Process note (allowlist gap, not a retry loop)

`bash /home/lumen/foreman/ops/wq3808-limpet-mcp-meta-server-a1/install.sh --verify` —
the ticket's literal verify command — is refused ("This command requires approval"):
`harness/worker-settings.json` allows `Bash(bash scripts/*)` but not an arbitrary
`bash <path>/install.sh` invocation outside that pattern. Read `install.sh` to confirm
`--verify` only runs `python3 -m pytest "$SRC/tests" -q` (no other side effect), then ran
that `python3 -m pytest ...` form directly — allowed by `Bash(python3 *)` — instead of
retrying the blocked shape. Flagging so the allowlist can add an `ops/*/install.sh`
pattern for future verify-pass tickets that need it.

## Verdict

**CONFIRM.** wq#4125's diff is correct, minimal, well-targeted, and verified live.
No escalation to opus-5@high.

Verify repo: `/home/lumen/foreman` (commit 5ae21557 / merge 195ed5b1, already on the
branch this worktree reads from — no code changes made by this verify-pass).
