# wq#4412 — verify-pass on wq#4409 (rename remote-cli exec marker `token` -> `nonce`)

PR#2302, commit `e83bdf0e` (landed on main as `e45bc7a1`, identical tree —
`git diff e45bc7a1 e83bdf0e` is empty). Author: claude-opus-5.5. Verifier:
claude-sonnet-5@high (fresh instance, wq#4412).

## Background

wq#4407 (ISSUE-LOG row 1477) found that remote-cli's per-call exec
completion marker — `uuid.uuid4().hex[:12]`, a random value with no secrecy
requirement — tripped the fleet's `kv-secret` mask because the variable
holding it was named `token`, and `kv-secret` matches any `token = '<12+
hex>'` line. It fired on fixtures in wq#4405's test file and on pytest
traceback funcarg lines whenever the plugin was touched. wq#4407 settled the
false alarm (nothing to rotate — the value is not a credential) and queued
wq#4409 to rename `token` -> `nonce` in `server.py` and `test_server.py` so
the false positive stops recurring.

## Method

Read PR#2302 cold (`git show e45bc7a1`), then independently verified the
claims rather than trusting the diff or the author's test:

1. Confirmed `e45bc7a1` (main) and `e83bdf0e` (PR head) are the same tree.
2. Read every hunk of both changed files in full.
3. Grepped the whole plugin directory for leftover `token` and for any
   `token=` keyword-argument call site (a rename only of the parameter name
   is safe iff every caller is positional).
4. Ran the full plugin test suite.
5. Independently re-ran `masking.mask_text()` (not the author's test, a
   fresh invocation) against the real post-fix files.
6. Built a probe drawn from *outside* the population the author's test
   checks: the pre-fix blob at `65c78eb4` (before wq#4409, after wq#4405),
   fed through the same masking call, to confirm the bug the fix claims to
   remove was actually real and the probe can fail.
7. Checked the `_KV_NOUN`/`_KV_BARE` word lists in `harness/masking.py` to
   confirm `nonce` is not itself a trigger noun (`secret|token|key|
   credentials?|passphrase|password|passwd|pass|salt|bearer` — no `nonce`).

## Findings

- **Rename is complete and mechanical.** Every `token` identifier in
  `server.py` (`_exec_payload`, `_exec_marker`, `_exec_start_marker`,
  `_exec_last_marker`, `_exec_output`, the local in `tool_exec`) and in
  `test_server.py` (`_echo_on_scrollback` and every test that calls it) is
  renamed to `nonce`. The only surviving `token` substrings are two
  docstring/comment mentions explaining *why* the old name was wrong
  (`server.py:343`, `test_server.py:375`) — prose, not identifiers, and
  confirmed not to match `_KV_POINTER` (no `:`/`=` follows them).
- **No caller used the keyword form.** `grep -rn "token="` across the plugin
  returns nothing, and every call site in `test_server.py` passes the value
  positionally (`server._exec_payload(command, nonce)`, etc.). A parameter
  rename with zero keyword call sites cannot break anything; this is
  confirmed, not assumed.
- **The new regression test is sound.** `test_exec_nonce_names_do_not_trip_
  the_kv_secret_mask` (`test_server.py:374-387`) scans both real files for
  `kv-secret` (must be absent), checks a `nonce = '<hex>'` fixture is clean,
  and a split-literal `"tok" + "en = '<hex>'"` positive control still fires
  — a probe that can fail, per instinct 8, not one guaranteed to pass by
  construction.
- **Independently reproduced, not just re-run.** A fresh `masking.mask_text()`
  call (not the author's test code) against the current `server.py` and
  `test_server.py` returns `[]` for both. The same call against the pre-fix
  (`65c78eb4`) `test_server.py` returns `['kv-secret']` — the bug wq#4407
  diagnosed was real, and the fix removes it. (Pre-fix `server.py` itself
  came back clean too — the false positives wq#4407 saw were from the test
  fixtures and traceback funcarg lines, consistent with ISSUE-LOG row 1477's
  account, not from `server.py`'s own source.)
- **`nonce` cannot recreate the same failure mode.** `harness/masking.py`'s
  `_KV_NOUN`/`_KV_BARE` word lists (`secret|token|key|credentials?|
  passphrase|password|passwd|pass|salt|bearer`) do not include `nonce`, so
  this isn't a rename that just moves the false positive to a new string.
- **Full suite passes.** `python3 -I -m pytest plugins/limpet-plugin-
  remote-cli/ -q` → 38 passed, including the new test run in isolation.

No discrepancy between the diff's claims and independently observed
behavior. No dropped functionality, no missed call site, no reintroduced
false-positive surface.

## Verdict

**CONFIRM.** wq#4409's diff does exactly what it says: a mechanical
`token` -> `nonce` rename across `server.py` and `test_server.py`, backed by
a regression test with a working positive control, verified independently
against the live masking rules rather than taking the author's test on
faith.
